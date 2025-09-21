import logging
import websockets
import asyncio
import json
import hmac
import time 
import hashlib
import base64

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("grid.log"),
        logging.StreamHandler()
    ]
)

class WebSocketClient:
	def __init__(self, api_key, secret_key, passphrase, instrument_id, price_queue: asyncio.Queue, orders_queue: asyncio.Queue):
		self.instrument_id = instrument_id
		self.price_queue = price_queue
		self.orders_queue = orders_queue

		self.public_url = "wss://ws.okx.com:8443/ws/v5/public"
		self.private_url = "wss://ws.okx.com:8443/ws/v5/private"
		self.api_key = api_key
		self.secret_key = secret_key
		self.passphrase = passphrase

		self.public_ws = None
		self.private_ws = None

		self.running = True
		self.reconnect_delay = 2

		self.public_task = None
		self.private_task = None

	async def connect_public(self):
		while self.running:
			try:
				async with websockets.connect(self.public_url) as ws:
					self.public_ws = ws
					await self.subscribe_public()
					await self.listen_public()
			except Exception as e:
				if not self.running:
					break
				logging.warning(f"❗️ Public WS error: {e}, reconnecting in {self.reconnect_delay}s")
				await asyncio.sleep(self.reconnect_delay)

	async def connect_private(self):
		while self.running:
			try:
				async with websockets.connect(self.private_url) as ws:
					self.private_ws = ws
					await self.login()
					await self.subscribe_private()
					await self.listen_private()
			except Exception as e:
				if not self.running:
					break
				logging.warning(f"❗️ Private WS error: {e}, reconnecting in {self.reconnect_delay}s")
				await asyncio.sleep(self.reconnect_delay)

	async def subscribe_public(self):
		msg = {
			"op": "subscribe",
			"args": [{"channel": "tickers", "instId": self.instrument_id}]
		}
		await self.public_ws.send(json.dumps(msg))
		logging.info(f"✅ Subscribed to public tickers for {self.instrument_id}")

	async def subscribe_private(self):
		msg = {
			"op": "subscribe",
			"args": [{"channel": "orders", "instType": "SWAP", "instId": self.instrument_id}]
		}
		await self.private_ws.send(json.dumps(msg))
		logging.info(f"✅ Subscribed to private orders for {self.instrument_id}")

	async def login(self):
		timestamp = str(time.time())
		method = "GET"
		request_path = "/users/self/verify"
		message = timestamp + method + request_path
		mac = hmac.new(self.secret_key.encode(), message.encode(), hashlib.sha256)
		sign = base64.b64encode(mac.digest()).decode()

		login_msg = {
			"op": "login",
			"args": [{
				"apiKey": self.api_key,
				"passphrase": self.passphrase,
				"timestamp": timestamp,
				"sign": sign
			}]
		}
		await self.private_ws.send(json.dumps(login_msg))

		async for msg in self.private_ws:
			data = json.loads(msg)
			if data.get("event") == "login" and data.get("code") == "0":
				logging.info("✅ Login successful")
				break
			elif data.get("event") == "login" and data.get("code") != "0":
				raise Exception(f"❗️Login failed: {data}")

	async def listen_public(self):
		async for msg in self.public_ws:
			data = json.loads(msg)
			if "arg" in data and data["arg"].get("channel") == "tickers":
				for tick in data.get("data", []):
					price = tick.get("last")
					# Кладём цену в очередь, очищая предыдущие, если есть
					if self.price_queue.full():
						_ = self.price_queue.get_nowait()
					await self.price_queue.put(float(price))
					# print(f"Public price updated: {price}")

	async def listen_private(self):
		async for msg in self.private_ws:
			data = json.loads(msg)
			if "arg" in data and data["arg"].get("channel") == "orders":
				orders = data.get("data", [])
				# Only enqueue when there are actual order updates
				if orders:
					# print(f"Private orders update: {orders}")
					await self.orders_queue.put(data)

	async def start(self):
		try:
			self.running = True
			# Launch both connect tasks
			self.public_task = asyncio.create_task(self.connect_public())
			self.private_task = asyncio.create_task(self.connect_private())
			# Wait until either task ends (e.g., on shutdown)
			if self.public_task and self.private_task:
				await asyncio.gather(self.public_task, self.private_task)
			elif self.private_task:
				await asyncio.gather(self.private_task)
		except Exception as e:
			logging.warning(e)

	async def shutdown(self):
		logging.info("🌀 Shutting down WebSocket manager...")
		self.running = False

		if self.public_ws:
			await self.public_ws.close()
		if self.private_ws:
			await self.private_ws.close()

		if self.public_task:
			self.public_task.cancel()
			try:
				await self.public_task
			except asyncio.CancelledError:
				pass
		if self.private_task:
			self.private_task.cancel()
			try:
				await self.private_task
			except asyncio.CancelledError:
				pass

		logging.info("✅ WebSocket manager stopped")
