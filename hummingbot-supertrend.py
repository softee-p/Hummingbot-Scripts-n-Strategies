from collections import deque
from statistics import mean
from decimal import Decimal
from typing import List, Optional
import time
import logging

# Custom implementation of the supertrend strategy for the Hummingbot Framework.

# The current config is for binance-paper-trade and you don't need an account to use it.
# It can be easily changed to any connector by changing the 'exchange' variable.
# The Loopring connector needs the order-book to be configured separately and is not included.


# The Average True Range (ATR) calculation method used is 'mean'
# This Script is unfinished is not meant to make you money.
# It is not adaptive to large market swings, it will just buy
# and sell depending on the signal and the chosen timeframe
# 
# From general Testing in Pinescript, the Supertrend Strategy
# has a better long-term profit on charts of 30m itnervals and up.
# The current buy/sell size is ALL. ALL-IN BABY


import requests
import pandas as pd
pd.set_option('display.max_rows', None)

import warnings
warnings.filterwarnings('ignore')

from hummingbot.connector.utils import split_hb_trading_pair
from hummingbot.core.data_type.common import PriceType, OrderType, TradeType
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase
from hummingbot.core.data_type.order_candidate import OrderCandidate
from hummingbot.core.event.events import (
  BuyOrderCompletedEvent,
  BuyOrderCreatedEvent,
  MarketOrderFailureEvent,
  OrderCancelledEvent,
  OrderFilledEvent,
  SellOrderCompletedEvent,
  SellOrderCreatedEvent,
)


class Script1(ScriptStrategyBase):
  exchange: str = "binance_paper_trade"
  trading_pair: str = "LRC-USDT"
  base_asset, quote_asset = split_hb_trading_pair(trading_pair)
  conversion_pair: str = f"{quote_asset}-USD"
  markets = {exchange: {trading_pair}}

  #ORDERS
  order_refresh_time: int = 15
  create_timestamp: int = 0
  side = TradeType.BUY
  price_source = PriceType.MidPrice
  position = False
  _filling_position: bool = False
  all_trades = []

  #CANDLES
  candle_limit: str = "40"
  candle_interval: str = "30m"
  candle_sec: int = int(candle_interval.split("m")[0]) * 60 + 2

  # strategy variables
  lookback: int = 12
  multiplier: float = 1.2
  buy_n_hold = []
  _cumulative_price_change_pct = Decimal(0)

  dfq = deque([], maxlen=1)
  first_tick: bool = True

  def swap_if_uneven(self):
    if self.first_tick:
      base_balance = self.connectors[self.exchange].get_available_balance(self.base_asset)
      quote_balance = self.connectors[self.exchange].get_available_balance(self.quote_asset)
      if base_balance >= quote_balance:
        self.position = True
        self.logger().info(f"Base asset balance is higher than quote. {self.base_asset} will be swapped for {self.quote_asset}")
    self.first_tick = False


  def on_tick(self):
    self.swap_if_uneven()
    if not self.dfq or int(time.time()) - self.dfq[0]['close_time'][int(self.candle_limit) - 2] > self.candle_sec:
      self.process_candles()
    df = self.dfq[0]

    # CHECK POSITION
    if df['in_uptrend'][int(self.candle_limit) - 2]:
      self.side = TradeType.BUY
      self.price_source = PriceType.BestAsk
      if self.position:
        return
    else:
      self.side = TradeType.SELL
      self.price_source = PriceType.BestBid
      if not self.position:
        return
      
    # TODO CHECK & UPDATE ORDERS *modify so that 1).old orders are kept if direction changes. 2)change to market order if stoploss.
    
    if self.create_timestamp <= self.current_timestamp:
      if df['signal'][int(self.candle_limit) - 2] != "---" or self.get_active_orders(connector_name=self.exchange):
        self.cancel_all_orders()
        proposal: List[OrderCandidate] = self.create_proposal()
        proposal_adjusted: List[OrderCandidate] = self.adjust_proposal_to_budget(proposal)
        self.place_orders(proposal_adjusted)
        self.create_timestamp = self.order_refresh_time + self.current_timestamp
  




  def cancel_all_orders(self):
    for order in self.get_active_orders(connector_name=self.exchange):
      self.cancel(self.exchange, order.trading_pair, order.client_order_id)
  
  def create_proposal(self) -> List[OrderCandidate]:
    quote_balance = self.connectors[self.exchange].get_available_balance(self.quote_asset)
    base_balance = self.connectors[self.exchange].get_available_balance(self.base_asset)
    best_price = self.connectors[self.exchange].get_price_by_type(self.trading_pair, self.price_source)

    if self.side == TradeType.BUY:
      order_amount = quote_balance / best_price
    elif self.side == TradeType.SELL:
      order_amount = base_balance
    
    order = OrderCandidate(
      trading_pair=self.trading_pair,
      is_maker=False,
      order_type=OrderType.LIMIT,
      order_side=self.side,
      amount=Decimal(order_amount),
      price=best_price
    )
    return [order]
    
  def adjust_proposal_to_budget(self, proposal: List[OrderCandidate]) -> List[OrderCandidate]:
    proposal_adjusted = self.connectors[self.exchange].budget_checker.adjust_candidates(proposal, all_or_none=False)
    return proposal_adjusted
  
  def place_orders(self, proposal: List[OrderCandidate]) -> None:
    for order in proposal:
      self.place_order(connector_name=self.exchange, order=order)

  def place_order(self, connector_name: str, order: OrderCandidate):
    is_buy = order.order_side == TradeType.BUY
    place_order = self.buy if is_buy else self.sell
    place_order(
      connector_name = connector_name,
      trading_pair=order.trading_pair,
      amount=order.amount,
      order_type=order.order_type,
      price=order.price
    )
  
  def _get_candles_df(self, trading_pair: str, intrvl: str, lmt: str):
    """
    Fetches binance candle stick data and returns a list daily close
    This is the API response data structure:
    [
      [
        1499040000000,      // Open time
        "0.01634790",       // Open
        "0.80000000",       // High
        "0.01575800",       // Low
        "0.01577100",       // Close
        "148976.11427815",  // Volume
        1499644799999,      // Close time
        "2434.19055334",    // Quote asset volume
        308,                // Number of trades
        "1756.87402397",    // Taker buy base asset volume
        "28.46694368",      // Taker buy quote asset volume
        "17928899.62484339" // Ignore.
      ]
    ]

    :param trading_pair: A market trading pair to

    :return: A list of daily close
    """

    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": trading_pair.replace("-", ""),
              "interval": intrvl, "limit": lmt}
    candles = requests.get(url=url, params=params).json()
    for candle in candles:
        candle[0] = str(candle[0])[:10]
        candle[6] = str(candle[6])[:10]
        # ignores last unfinished candle
    df = pd.DataFrame(candles[:-1], columns=[
        'open_time',
        'open',
        'high',
        'low',
        'close',
        'volume',
        'close_time',
        'quote_volume',
        'trades',
        'taker_buy_base_vol',
        'taker_buy_quote_vol',
        'ignore'
    ]).astype(float)
    return df
  
  def process_candles(self):
    
    df = self._get_candles_df(self.trading_pair, self.candle_interval, self.candle_limit)

    # TRUE-RANGE
    df['prev_close'] = df['close'].shift(1)
    df['high-low'] = df['high'] - df['low']
    df['high-prev'] = abs(df['high'] - df['prev_close'])
    df['low-prev'] = abs(df['low'] - df['prev_close'])
    df['tr'] = df[['high-low', 'high-prev', 'low-prev']].max(axis=1)

    # ATR
    # second calculation method:
    # df['atr'] = df['tr'].ewm(lookback).mean()
    df['atr'] = df['tr'].rolling(self.lookback).mean()

    # BANDS
    avg_hl = (df['high'] + df['low']) / 2
    df['upperband'] = avg_hl + (self.multiplier * df['atr'])
    df['lowerband'] = avg_hl - (self.multiplier * df['atr'])
    df['in_uptrend'] = True
    df['signal'] = "---"
    for current in range(1, len(df.index)):
      previous = current - 1
      if df['close'][current] > df['upperband'][previous]:
        df['in_uptrend'][current] = True
      elif df['close'][current] < df['lowerband'][previous]:
        df['in_uptrend'][current] = False
      else:
        df['in_uptrend'][current] = df['in_uptrend'][previous]
        if df['in_uptrend'][current] and df['lowerband'][current] < df['lowerband'][previous]:
          df['lowerband'][current] = df['lowerband'][previous]
        if not df['in_uptrend'][current] and df['upperband'][current] > df['upperband'][previous]:
          df['upperband'][current] = df['upperband'][previous]

      # SIGNAL
      if not df['in_uptrend'][previous] and df['in_uptrend'][current]:
        df['signal'][current] = 'buy'
      elif df['in_uptrend'][previous] and not df['in_uptrend'][current]:
        df['signal'][current] = 'sell'
      else:
        df['signal'][current] = "---"
    
    self.dfq.append(df)
    self.logger().info("New candle processed.")
    self.logger().info(f"In Uptrend: {df[['close', 'in_uptrend', 'signal']]}")

  #----------------------------------------------------EVENTS-----------------------------------------------------------------#

  def did_fill_order(self, event: OrderFilledEvent):
    """
    Indicate that position is filled, save position properties on enter, calculate cumulative price change on exit.
    """
    if event.trade_type == TradeType.BUY:
      self.position = event
      self._filling_position = False
    elif event.trade_type == TradeType.SELL:
      if isinstance(self.position, OrderFilledEvent):
        delta_price = (event.price - self.position.price) / self.position.price
        self._cumulative_price_change_pct += delta_price
      self.position = False
      self._filling_position = False
    else:
      self.logger().warn(f"Unsupported order type filled: {event.trade_type}")
  

  def did_create_buy_order(self, event: BuyOrderCreatedEvent):
    """
    Method called when the connector notifies a buy order has been created
    """
    if not self.buy_n_hold:
      self.buy_n_hold.append(event)
    
    self.logger().info(logging.INFO, f"The buy order {event.order_id} has been created")
  

  def did_create_sell_order(self, event: SellOrderCreatedEvent):
    """
    Method called when the connector notifies a sell order has been created
    """
    self.logger().info(logging.INFO, f"The sell order {event.order_id} has been created")
  

  def did_fail_order(self, event: MarketOrderFailureEvent):
    """
    Method called when the connector notifies an order has failed
    """
    self._filling_position = False
    self.logger().info(logging.INFO, f"The order {event.order_id} failed")

  def did_cancel_order(self, event: OrderCancelledEvent):
    """
    Method called when the connector notifies an order has been cancelled
    """
    self._filling_position = False
    self.logger().info(f"The order {event.order_id} has been cancelled")

  def did_complete_buy_order(self, event: BuyOrderCompletedEvent):
    """
    Method called when the connector notifies a buy order has been completed (fully filled)
    """
    self.all_trades.append(event)
    self.logger().info(f"The buy order {event.order_id} has been completed")

  def did_complete_sell_order(self, event: SellOrderCompletedEvent):
    """
    Method called when the connector notifies a sell order has been completed (fully filled)
    """
    self.all_trades.append(event)
    self.logger().info(f"The sell order {event.order_id} has been completed")

  
  def format_status(self) -> str:
    """
    Returns status of the current strategy on user balances and current active orders. This function is called
    when status command is issued. Override this function to create custom status display output.
    """
    if not self.ready_to_trade:
      return "Market connectors are not ready."
    lines = []
    warning_lines = []
    warning_lines.extend(self.network_warning(self.get_market_trading_pair_tuples()))

    balance_df = self.get_balance_df()
    lines.extend(["", "  Balances:"] + ["    " + line for line in balance_df.to_string(index=False).split("\n")])

    try:
      df = self.active_orders_df()
      lines.extend(["", "  Orders:"] + ["    " + line for line in df.to_string(index=False).split("\n")])
    except ValueError:
      lines.extend(["", "  No active orders."])
    
    # Strategy specific info
    if self.buy_n_hold:
      price_then = self.buy_n_hold[0].price
      price_now = self.connectors[self.exchange].get_price_by_type(self.trading_pair, PriceType.BestAsk)
      bh_change = ((price_now - price_then) / price_then) * 100
    else:
      bh_change = None
    lines.extend(["", "  Supertrend strategy total change with all trades:"] + ["    " + f"{self._cumulative_price_change_pct:.5f}" + " %"])
    lines.extend(["", "  Buy & Hold total change: ", "  " + str(bh_change)[0:8] + " %"])
    
    status_position = True if self.position else False
    lines.extend(["", "  Position: ", "  " + str(status_position)])

    if self.all_trades:
      lines.extend(["", "  Trades:"])
      for trade in self.all_trades:
        trade_side = "SELL" if isinstance(trade, BuyOrderCompletedEvent) else "BUY"
        lines.extend(["  " + f"{trade.timestamp}   {trade.order_type} {trade_side}   base: {trade.base_asset_amount} quote: {trade.quote_asset_amount}"])
    else:
      lines.extend(["", "  Trades: None"])
    

    warning_lines.extend(self.balance_warning(self.get_market_trading_pair_tuples()))
    if len(warning_lines) > 0:
      lines.extend(["", "*** WARNINGS ***"] + warning_lines)
    return "\n".join(lines)