#!/usr/bin/env python3
import asyncio
from asyncio import QueueEmpty
from datetime import datetime
import logging
from ws_okx import WebSocketClient
from trade_okx import Trading
from tech import TechAnalysis
from telegram_bot import TelegramBot


# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# === User and Coin data ===
from keys import *
inst_id = "DEGEN-USDT-SWAP"
balance = 50
leverage = 2
allowed_user = "floppa_lohnes"
chat_id = None
tf = 1  # Timeframe in minutes, 1 = 1 minute candles, 24 = 1day candles
trading = None # type: ignore

# === Cancel all tasks after TgBot Button ====
async def full_shutdown():
	# 1. Shutdown websocket
	await ws.shutdown()
	# 2. Cancel all other asyncio tasks except this one
	current_task = asyncio.current_task()
	tasks = [t for t in asyncio.all_tasks() if t is not current_task]
	for task in tasks:
		task.cancel()

	# 4. Log completion
	logging.info("✅Full shutdown completed: ws closed, orders canceled, tasks canceled.")
	for handler in logging.root.handlers[:]:
		handler.close()
		logging.root.removeHandler(handler)

# === Buy line updater coroutine ===
async def sma_updater():
	
	await asyncio.sleep(4)  # Give some time for the program to connect Websockets
	while True:
		if not tg_bot.bot_work:
			await asyncio.sleep(4)
			continue
		try:
			# Проверяем: если нет ни одного ордера в статусе 'filled' или 'partially_filled'	
			if not any(order['status'] in ['filled', 'partially_filled'] for order in trading.strategy_orders.values()):
				candles = await ta.get_candle_data()
				sma = await ta.calculate_sma(candles, length=14)
				buy_grid = await trading.get_buy_grid(start_from=sma, quantity=100)
				sell_grid = await trading.get_sell_grid(buy_orders=buy_grid)
				
			now = datetime.now()

			# Вычисляем количество минут, прошедших с начала часа
			minute = now.minute
			second = now.second
			elapsed = (minute % tf) * 60 + second
			interval_seconds = tf * 60
			seconds = interval_seconds - elapsed

			await asyncio.sleep(seconds + 1)

		except Exception as e:
			logging.warning(f"😈Ошибка обновления sma_updater: {e}")

# === Strategy coroutine ===
async def strategy(price_queue: asyncio.Queue, orders_queue: asyncio.Queue):
	await asyncio.sleep(4)  # Give some time for the program to connect Websockets
	orders_cancelled = False
	while True:
				
		# --- Stop function if we pressed TgBot Pause Button ---
		if not tg_bot.bot_work:
			if trading.strategy_orders and not orders_cancelled:
				trading.buy_grid_orders.clear()
				trading.sell_grid_orders.clear()
				trading.strategy_orders.clear()
				orders_cancelled = True

			await asyncio.sleep(4)
			continue
		else:
			# Сброс флага, если бот снова запущен
			orders_cancelled = False

		# --- Getting price from queue and analyse it ---
		try:
			if not hasattr(strategy, "_last_price"):
				strategy._last_price = None
			price = await price_queue.get()
			if price != strategy._last_price:
				strategy._last_price = price

				# === BUY GRID ORDERS ===
				if hasattr(trading, 'buy_grid_orders'):
					for order_number, order in list(trading.buy_grid_orders.items()):
						if order['status'] == 'live' and price <= order['entry_price']:
							
							logging.info(f"🟢🟢🟢🟢🟢🟢🟢Buy {order['size']} {inst_id} | {price}, entry_price {order['entry_price']}. Close price {order['close_price']}. Order {order_number}")

							# Размещаем маркет ордер на покупку
							market_order_id = await trading.place_market_buy_order(order, price)
							
							# Обновляем статус ордера в buy_grid_orders
							order['status'] = 'filled'

							# Активируем соответствующий sell ордер
							trading.sell_grid_orders[order_number]['status'] = 'live'
							trading.sell_grid_orders[order_number]['group_with_id'] = market_order_id
							
				# === SELL GRID ORDERS ===
				if hasattr(trading, 'sell_grid_orders'):
					for order_number, order in list(trading.sell_grid_orders.items()):
						if order['status'] == 'live' and price >= order['entry_price']:
							
							logging.info(f"🔴🔴🔴🔴🔴🔴🔴Sell {order['size']} {inst_id} | {price}, entry_price {order['entry_price']}. Order {order_number}")
							
							order_copy = order.copy()
							order_copy['group_with_id'] = order['group_with_id']  # Это уже реальный ID после обновления в buy блоке
							market_order_id = await trading.place_market_sell_order(order_copy, price)

							# Обновляем статус ордера в sell_grid_orders
							order['status'] = 'filled'
							
							# Проверяем, если продали первый ордер  - сбрасываем все
							if order_number == 0: 
								logging.info("🔶Cбрасываем все и пересчитываем buyline")
								
								trading.strategy_orders.clear()
								trading.buy_grid_orders.clear()
								trading.sell_grid_orders.clear()

								try:
									await tg_bot.send_message(
										tg_bot.chat_id,
										"✅ Setup Done"
									)
								except Exception as e:
									logging.warning(f"🔅❌ Failed to send reset notification: {e}")

							else:
								# Если продали не первый ордер - активируем соответствующий buy ордер
								trading.buy_grid_orders[order_number]['status'] = 'live'
								trading.strategy_orders[market_order_id]['status'] = 'live'

				else: 
					logging.info(f'ERROR: Dont have buy/sell grid orders')
				
		except RuntimeError as e:
			if "attached to a different loop" in str(e):
				logging.warning("Price queue attached to a different event loop. Exiting strategy loop.")
				break
			else:
				logging.info(f"Price queue error: {e}")
				raise

		# --- Process order events and update orders ---
		while True:
			try:
				msg = orders_queue.get_nowait()
			except QueueEmpty:
				break

			# Process the message about orders
			data = msg.get("data", [])
			if not data:	
				continue

			# # ---  Checking order Status  ---
			for item in data:
				order_id = item.get("ordId")
				state = item.get("state")

				if state == "filled" or state == "partially_filled":
					if order_id in trading.strategy_orders:
						trading.strategy_orders[order_id]["status"] = state
						side = trading.strategy_orders[order_id].get("side")
						trading.strategy_orders[order_id]['size'] = float(item.get('accFillSz'))
						trading.strategy_orders[order_id]['filledPrice'] = float(item.get('avgPx'))
						trading.strategy_orders[order_id]['usdt_size'] = float(item.get('notionalUsd'))
						trading.strategy_orders[order_id]['fee'] = float(item.get('fee'))

						# Send TgBot message about filled order
						try:
							if tg_bot.chat_id:
								filled_price = item.get('avgPx')
								usdt_size = float(item.get('notionalUsd', 0))
								side_emoji = "🛒" if side == "buy" else "💰"
								side_text = "Buy" if side == "buy" else "Sell"
								
								# Добавляем (partial) если статус partially_filled
								if state == "partially_filled":
									side_text += " (partial)"
								
								await tg_bot.send_message(
									tg_bot.chat_id,
									f"{side_emoji} {side_text} at {filled_price} | {round(usdt_size, 2)} USDT"
								)
							else:
								logging.warning("🔅❌ tg_bot.chat_id is None, skipping notification")
						except Exception as e:
							logging.warning(f"🔅❌ Failed to send order notification: {e}")
						
# === Initialize all objects and creating (waiting) async tasks ===
async def create_tasks():
	global tg_bot, trading, ta, ws
	price_queue = asyncio.Queue()
	orders_queue = asyncio.Queue()

	# === Getting instrument parameters from exchange ===
	ta = TechAnalysis(instrument_id=inst_id, lookback=15, timeframe=tf)
	lot_size, ct_val, min_size, tick_size = await ta.get_lot_tick_min()
	ta.tick_size = tick_size
	ws = WebSocketClient(api_key=api_key, 
						 secret_key=secret_key, 
						 passphrase=passphrase,
						 instrument_id=inst_id,
						 price_queue=price_queue, 
						 orders_queue=orders_queue
						 )
	trading = Trading(api_key=api_key, 
				  secret_key=secret_key, 
				  passphrase=passphrase,
				  instrument_id=inst_id,
				  balance=balance, 
				  leverage=leverage,
				  lot_size=lot_size, ct_val=ct_val, min_size=min_size, tick_size=tick_size,
				  grid_step=0.003, profit_target=0.004
				  )
	tg_bot = TelegramBot(shutdown_coroutine=full_shutdown,
							 tg_token=tg_token,
							 trading=trading, 
							 allowed_user=allowed_user,
							 chat_id=chat_id
						)
	logging.info(f"lot_precision: {trading.lot_precision}")
	tasks = [
		asyncio.create_task(ws.start()),
		asyncio.create_task(tg_bot.start()),
		asyncio.create_task(sma_updater()),
		asyncio.create_task(strategy(price_queue, orders_queue)),
	]
	try:
		await asyncio.wait(tasks)
	except asyncio.CancelledError:
		pass

if __name__ == '__main__':
	try:
		asyncio.run(create_tasks())
	except RuntimeError as e:
		# Ignore loop closed before all tasks completed
		if 'Event loop stopped before Future completed.' not in str(e):
			raise
	except KeyboardInterrupt:
		pass