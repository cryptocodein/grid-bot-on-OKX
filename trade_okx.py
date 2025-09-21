from datetime import datetime
import logging
import okx.Trade as Trade

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("grid.log"),
        logging.StreamHandler()
    ]
)

class Trading:
	def __init__(self, api_key: str, secret_key: str, passphrase: str, balance: float, leverage: float, instrument_id: str, lot_size: float, ct_val: float, min_size: float, tick_size: float, grid_step: float, profit_target: float):
		self.tradeAPI = Trade.TradeAPI(api_key, secret_key, passphrase, False, '0')
		self.balance = balance
		self.leverage = leverage
		self.instrument_id = instrument_id
		self.lot_size = lot_size
		self.ct_val = ct_val
		self.min_size = min_size
		self.tick_size = tick_size
		self.strategy_orders = {}
		self.first_order_id = None
		self.grid_step = grid_step
		self.profit_target = profit_target
		self.buy_grid_orders = {}
		self.sell_grid_orders = {}

		def get_precision(value):
			value_str = f'{value:.16f}'.rstrip('0')
			if '.' in value_str:
				return max(0, len(value_str.split('.')[-1]))
			return 0
		self.lot_precision = get_precision(self.lot_size)

	def round_tick(self, value: float, tick_size: float = None) -> float:
		"""
		Округляет value по количеству знаков после запятой, соответствующему tick_size.
		Если tick_size не задан, округляет до 2 знаков.
		"""
		if tick_size is None:
			return round(value, 2)
		if tick_size >= 1:
			decimals = 0
		else:
			decimals = len(str(tick_size).split('.')[-1].rstrip('0'))
		return round(value, decimals)

	async def get_buy_grid(self, start_from: float, quantity: int) -> dict:

		buy_grid_orders = {}
		orders_have = 0  # для совместимости с оригинальной функцией
		
		for i in range(quantity):
			index = orders_have + i  # абсолютный номер ордера в сетке

			usdt_amount = self.balance * 0.01 # 1% от баланса			
			px = self.round_tick(start_from * (1 - self.grid_step * index), self.tick_size)

			# Расчет размера ордера с учетом ct_val и lot_size
			contracts = usdt_amount / (px * self.ct_val)
			lots = contracts / self.lot_size
			size_i = lots * self.lot_size
			# Если у нас размер ордера меньше размера минимального объема
			if size_i < self.min_size:
				size_i = self.min_size
			size_i = self.round_tick(size_i, self.lot_size)
			

			buy_grid_orders[i] = {
				'entry_price': px,
				'close_price': self.round_tick(px * (1 + self.profit_target), self.tick_size),
				'size': size_i,
				'crTime': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
				'status': 'live', 
				'side': 'buy',
				'group_with_id': None,
				'filledTime': None,
				'filledPrice': None,
				'order_index': index,
			}

		
		# Красивый вывод сетки
		# logging.info("\n" + "="*80)
		# logging.info(f"{'№':<3} {'Цена входа':<12} {'Цена закрытия':<14} {'Объем':<12}")
		# logging.info("="*80)
		# for order_id, order in buy_grid_orders.items():
		# 	logging.info(f"{order_id:<3} {order['entry_price']:<12} {order['close_price']:<14} {order['size']:<12}")
		# logging.info("="*80 + "\n")

		logging.info(f"🔶Grid calculated: {quantity} orders starting from price {start_from}")
		self.buy_grid_orders = buy_grid_orders
		return buy_grid_orders

	async def get_sell_grid(self, buy_orders: dict) -> dict:
		sell_grid_orders = {}
		
		for buy_order_number, buy_order in buy_orders.items():
			price = buy_order['close_price']  # цена закрытия из buy ордера
			order_index = buy_order.get('order_index', 0)
			size = buy_order['size']
			
			
			# Создаем sell ордер
			sell_grid_orders[buy_order_number] = {
				'entry_price': price,
				'close_price': None,
				'size': size,
				'crTime': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
				'status': 'calculated', 
				'side': 'sell',
				'group_with_id': buy_order_number,
				'filledTime': None,
				'filledPrice': None,
				'order_index': order_index,
			}
		
		# Красивый вывод sell сетки
		# logging.info("\n" + "="*80)
		# logging.info(f"{'№':<3} {'Цена входа':<12} {'Объем':<12} {'Группа с':<8}")
		# logging.info("="*80)
		# for order_id, order in sell_grid_orders.items():
		# 	logging.info(f"{order_id:<3} {order['entry_price']:<12} {order['size']:<12} {order['group_with_id']:<8}")
		# logging.info("="*80 + "\n")
		
		logging.info(f"🔶Sell grid calculated: {len(sell_grid_orders)} orders")
		self.sell_grid_orders = sell_grid_orders
		return sell_grid_orders

	async def place_market_buy_order(self, order_data: dict, price) -> str:
		try:
			market_order = self.tradeAPI.place_order(
				instId=self.instrument_id,
				tdMode="isolated",
				side='buy',
				ccy="USDT",
				ordType="market",
				sz=str(round(order_data['size'], self.lot_precision)),
			)
			
			if market_order.get("code") == "0":
				logging.info(market_order)
				ord_id = market_order['data'][0]['ordId']
				filled_size = float(market_order['data'][0].get('sz', order_data['size']))
				
				# Добавляем полную информацию в strategy_orders
				self.strategy_orders[ord_id] = {
					'entry_price': order_data['entry_price'],
					'close_price': order_data['close_price'],
					'size': filled_size,
					'crTime': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
					'status': 'filled',
					'side': 'buy',
					'group_with_id': None,
					'filledTime': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
					'filledPrice': price,
					'order_index': order_data.get('order_index', 0),
					'market_order_id': ord_id,
					'usdt_size': None, 
					'fee': None, 
				}
				
				# Если это первый ордер (order_index = 0), назначаем first_order_id
				if order_data.get('order_index', 0) == 0:
					self.first_order_id = ord_id
					logging.info(f"First order ID set: {self.first_order_id}")
				
				return ord_id
			else:
				logging.warning(f"❌Failed to place market buy order: {market_order.get('msg')}")
				return None
				
		except Exception as e:
			logging.warning(f"❌Error placing market buy order: {e}")
			return None

	async def place_market_sell_order(self, order_data: dict, price) -> str:
		try:
			market_order = self.tradeAPI.place_order(
				instId=self.instrument_id,
				tdMode="isolated",
				side='sell',
				ccy="USDT",
				ordType="market",
				sz=str(round(order_data['size'], self.lot_precision)),
				reduceOnly='true',
			)
			
			if market_order.get("code") == "0":
				ord_id = market_order['data'][0]['ordId']
				filled_size = float(market_order['data'][0].get('sz', order_data['size']))
				
				# Добавляем полную информацию в strategy_orders
				self.strategy_orders[ord_id] = {
					'entry_price': order_data['entry_price'],
					'close_price': order_data['close_price'],
					'size': filled_size,
					'crTime': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
					'status': 'filled',
					'side': 'sell',
					'group_with_id': order_data.get('group_with_id', None),
					'filledTime': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
					'filledPrice': price,
					'order_index': order_data.get('order_index', 0),
					'market_order_id': ord_id,
					'usdt_size': None, 
					'fee': None, 
				}
				
				return ord_id
			else:
				logging.warning(f"❌Failed to place market sell order: {market_order.get('msg')}")
				return None
				
		except Exception as e:
			logging.warning(f"❌Error placing market sell order: {e}")
			return None
		