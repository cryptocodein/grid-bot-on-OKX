from okx.api import Market
from okx.api import Public
import asyncio

import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("grid.log"),
        logging.StreamHandler()
    ]
)

class TechAnalysis:
	def __init__(self, instrument_id: str, lookback: int, timeframe):
		# Map numeric timeframes to OKX string format
		tf_map = {
			1: "1m",
			3: "3m",
			5: "5m",
			15: "15m",
			30: "30m",
			60: "1H",
			120: "2H",
			240: "4H",
			24: '1D'
		}
		self.instrument_id = instrument_id
		self.marketDataAPI = Market(flag='0')	
		self.publicDataAPI = Public(flag='0')
		self.lookback = lookback  
		self.midpoint_05 = None
		# Convert numeric timeframe to string if needed
		if isinstance(timeframe, int):
			if timeframe not in tf_map:
				raise ValueError(f"Invalid timeframe value: {timeframe}. Allowed: {list(tf_map.keys())}")
			self.timeframe = tf_map[timeframe]
		else:
			self.timeframe = timeframe

	# Получаем необработанные данные по свечам
	async def get_candle_data(self) -> dict:
		try:
			candle_data = self.marketDataAPI.get_candles(
				instId=self.instrument_id,
				bar=self.timeframe,
				limit=str(94)
			)
			return candle_data
		except Exception as e:
			logging.warning(f"Error fetching candle data: {e}")
			return []

	# Должны передать сухие данные по свечам
	async def calculate_sma(self, candles: dict, price_type: str = "close", length: int = 14) -> float:
		if "data" not in candles or len(candles["data"]) < length:
			logging.warning("Недостаточно данных для расчёта SMA")

		index_map = {
			"open": 1,
			"high": 2,
			"low": 3,
			"close": 4
		}

		idx = index_map.get(price_type.lower())
		if idx is None:
			logging.warning(f"Недопустимый тип цены для SMA: {price_type}. Используй: close, open, high, low.")

		prices = [float(c[idx]) for c in candles["data"][:length]]
		sma = sum(prices) / length
		
		return self.round_tick(sma, self.tick_size)

	async def get_lot_tick_min(self, inst_type: str = "SWAP") -> tuple:
		res = self.publicDataAPI.get_instruments(instType=inst_type, instId=self.instrument_id)
		if "data" not in res or not res["data"]:
			logging.warning("Не удалось получить данные инструмента")
			return None, None, None, None
		info = res["data"][0]
		lot_size = float(info["lotSz"])
		ct_val = float(info["ctVal"])
		tick_size = float(info["tickSz"])
		min_size = float(info["minSz"])
		logging.info(f"lot_size: {lot_size}, ct_val: {ct_val}, min_size: {min_size}, tick_size: {tick_size}")
		return lot_size, ct_val, min_size, tick_size

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


