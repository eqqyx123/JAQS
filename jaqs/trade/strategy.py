# encoding: utf-8

import abc
from abc import abstractmethod
from six import with_metaclass
from collections import defaultdict

import numpy as np

from jaqs.trade.gateway import PortfolioManager
from jaqs.data.basic.order import *
from jaqs.data.basic.position import GoalPosition
from jaqs.util.sequence import SequenceGenerator

from jaqs.trade import model
from jaqs.trade import common
from jaqs.trade.event import EventEngine
from jaqs.trade.pubsub import Subscriber
from jaqs.trade.event import eventType


class Strategy(with_metaclass(abc.ABCMeta)):
    """
    Abstract base class for strategies.

    Attributes
    ----------
    ctx : Context object
        Used to store relevant context of the strategy.
    run_mode : int
        Whether the strategy is under back-testing or live trading.
    trade_date : int
        current trading date (may be inconsistent with calendar date).
    pm : trade.PortfolioManger
        Responsible for managing orders, trades and positions.

    Methods
    -------

    """
    
    def __init__(self):
        super(Strategy, self).__init__()
        self.ctx = None
        self.run_mode = common.RUN_MODE.BACKTEST
        
        self.pm = PortfolioManager(self)

        self.task_id_map = defaultdict(list)
        self.seq_gen = SequenceGenerator()

        self.init_balance = 0.0
    
    @abc.abstractmethod
    def init_from_config(self, props):
        pass
    
    def initialize(self, run_mode):
        self.run_mode = run_mode
        # self.register_callback()
        pass
    
    """
    def register_callback(self):
        gw = self.context.gateway
        gw.register_callback('portfolio manager', self.pm)
        gw.register_callback('on_trade_ind', self.on_trade_ind)
        gw.register_callback('on_order_status', self.on_trade_ind)
    
    """
    def on_new_day(self, trade_date):
        pass
    
    def _get_next_num(self, key):
        """used to generate id for orders and trades."""
        return str(np.int64(self.ctx.trade_date) * 10000 + self.seq_gen.get_next(key))

    def place_order(self, symbol, action, price, size, algo="", algo_param=None):
        """
        Send a request with an order to the system. Execution algorithm will be automatically chosen.
        Returns task_id which can be used to query execution and orders of this task.

        Parameters
        ----------
        symbol : str
            the symbol of symbol to be ordered, eg. "000001.SZ".
        action : str
        price : float.
            The price to be ordered at.
        size : int
            The quantity to be ordered at.
        algo : str
            The algorithm to be used. If None then use default algorithm.
        algo_param : dict
            Parameters of the algorithm. Default {}.

        Returns
        -------
        task_id : str
            Task ID generated by entrust_order.
        err_msg : str.

        """
        if algo:
            raise NotImplementedError("algo {}".format(algo))
        
        order = Order.new_order(symbol, action, price, size, self.ctx.trade_date, 0)
        order.task_id = self._get_next_num('task_id')
        order.entrust_no = self._get_next_num('entrust_no')
        
        self.task_id_map[order.task_id].append(order.entrust_no)
        
        self.pm.add_order(order)
        
        err_msg = self.ctx.gateway.place_order(order)
        
        if err_msg:
            return '0', err_msg
        else:
            return order.task_id, err_msg
    
    def cancel_order(self, task_id):
        """Cancel all uncome orders of a task according to its task ID.

        Parameters
        ----------
        task_id : str
            ID of the task.
            NOTE we CANNOT cancel order by entrust_no because this may break the execution of algorithm.

        Returns
        -------
        result : str
            Indicate whether the cancel succeed.
        err_msg : str

        """
        entrust_no_list = self.task_id_map.get(task_id, None)
        if entrust_no_list is None:
            return False, "No task id {}".format(task_id)
        
        err_msgs = []
        for entrust_no in entrust_no_list:
            err_msg = self.ctx.gateway.cancel_order(entrust_no)
            err_msgs.append(err_msg)
        if any(map(lambda s: bool(s), err_msgs)):
            return False, ','.join(err_msgs)
        else:
            return True, ""
    
    def place_batch_order(self, orders, algo="", algo_param=None):
        """Send a batch of orders to the system together.

        Parameters
        -----------
        orders : list
            a list of trade.model.Order objects.
        algo : str
            The algorithm to be used. If None then use default algorithm.
        algo_param : dict
            Parameters of the algorithm. Default {}.

        Returns
        -------
        task_id : str
            Task ID generated by entrust_order.
        err_msg : str.

        """
        task_id = self._get_next_num('task_id')
        err_msgs = []
        for order in orders:
            # only add task_id and entrust_no, leave other attributes unchanged.
            order.task_id = task_id
            order.entrust_no = self._get_next_num('entrust_no')
            
            self.pm.add_order(order)
            
            err_msg = self.ctx.gateway.place_order(order)
            err_msgs.append(err_msg)
            
            self.task_id_map[order.task_id].append(order.entrust_no)
        
        return task_id, ','.join(err_msgs)
    
    def query_portfolio(self):
        """
        Return net positions of all securities in the strategy universe (including zero positions).

        Returns
        --------
        positions : list of trade.model.Position}
            Current position of the strategy.
        err_msg : str

        """
        pass
    
    def goal_portfolio(self, goals):
        """
        Let the system automatically generate orders according to portfolio positions goal.
        If there are uncome orders of any symbol in the strategy universe, this order will be rejected. #TODO not impl

        Parameters
        -----------
        goals : list of GoalPosition
            This must include positions of all securities in the strategy universe.
            Use former value if there is no change.

        Returns
        --------
        result : bool
            Whether this command is accepted. True means the system's acceptance, instead of positions have changed.
        err_msg : str

        """
        assert len(goals) == len(self.ctx.universe)
        
        orders = []
        for goal in goals:
            sec, goal_size = goal.symbol, goal.size
            if sec in self.pm.holding_securities:
                curr_size = self.pm.get_position(sec, self.ctx.trade_date).curr_size
            else:
                curr_size = 0
            diff_size = goal_size - curr_size
            if diff_size != 0:
                action = common.ORDER_ACTION.BUY if diff_size > 0 else common.ORDER_ACTION.SELL
                
                order = FixedPriceTypeOrder.new_order(sec, action, 0.0, abs(diff_size), self.ctx.trade_date, 0)
                order.price_target = 'vwap'  # TODO
                
                orders.append(order)
        self.place_batch_order(orders)
    
    def query_order(self, task_id):
        """
        Query order information of current day.

        Parameters
        ----------
        task_id : str
            ID of the task. if None, return all orders of the day; else return orders of this task.

        Returns
        -------
        orders : list of trade.model.Order objects.
        err_msg : str.

        """
        pass
    
    def query_trade(self, task_id):
        """
        Query trade information of current day.

        Parameters
        -----------
        task_id : int
            ID of the task. if None, return all trades of the day; else return trades of this task.

        Returns
        --------
        trades : list of trade.model.Trade objects.
        err_msg : str.

        """
        pass
    
    def on_trade_ind(self, ind):
        """

        Parameters
        ----------
        ind : TradeInd

        Returns
        -------

        """
        self.pm.on_trade_ind(ind)

    def on_order_status(self, ind):
        """

        Parameters
        ----------
        ind : OrderStatusInd

        Returns
        -------

        """
        self.pm.on_order_status(ind)


class AlphaStrategy(Strategy, model.FuncRegisterable):
    """
    Alpha strategy class.

    Attributes
    ----------
    period : str
        Interval between current and next. {'day', 'week', 'month'}
    days_delay : int
        n'th business day after next period.
    weights : np.array with the same shape with self.context.universe
    benchmark : str
        The benchmark symbol.
    risk_model : model.RiskModel
    revenue_model : model.ReturnModel
    cost_model : model.CostModel

    Methods
    -------

    """
    # TODO register context
    def __init__(self, revenue_model=None, stock_selector=None,
                 cost_model=None, risk_model=None,
                 pc_method="equal_weight"):
        super(AlphaStrategy, self).__init__()
        
        self.period = ""
        self.n_periods = 1
        self.days_delay = 0
        self.cash = 0
        self.position_ratio = 0.0
        
        self.risk_model = risk_model
        self.revenue_model = revenue_model
        self.cost_model = cost_model
        self.stock_selector = stock_selector
        
        self.weights = None
        
        self.benchmark = ""
        
        self.goal_positions = None
        
        self.pc_method = pc_method
        
        self.market_value_list = []

    def init_from_config(self, props):
        Strategy.init_from_config(self, props)
        
        self.cash = props.get('init_balance', 100000000)
        self.period = props.get('period', 'month')
        self.days_delay = props.get('days_delay', 0)
        self.n_periods = props.get('n_periods', 1)
        self.position_ratio = props.get('position_ratio', 0.98)

        self.use_pc_method(name='equal_weight', func=self.equal_weight, options=None)
        self.use_pc_method(name='mc', func=self.optimize_mc, options={'util_func': self.util_net_revenue,
                                                                           'constraints': None,
                                                                           'initial_value': None})
        self.use_pc_method(name='factor_value_weight', func=self.factor_value_weight, options=None)
        
        self._validate_parameters()
        print "AlphaStrategy Initialized."
    
    def _validate_parameters(self):
        if self.pc_method in ['mc', 'quad_opt']:
            if self.revenue_model is None and self.cost_model is None and self.risk_model is None:
                raise ValueError("At least one model of revenue, cost and risk must be provided.")
        elif self.pc_method in ['factor_value_weight']:
            if self.revenue_model is None:
                raise ValueError("revenue_model must be provided when pc_method = 'factor_value_weight'")
        elif self.pc_method in ['equal_weight']:
            pass
        else:
            raise NotImplementedError("pc_method = {:s}".format(self.pc_method))
    
    def on_trade_ind(self, ind):
        """

        Parameters
        ----------
        ind : TradeInd

        Returns
        -------

        """
        self.pm.on_trade_ind(ind)
        # print str(ind)
        
    def use_pc_method(self, name, func, options=None):
        self._register_func(name, func, options)
    
    def _get_weights_last(self):
        current_positions = self.query_portfolio()
        univ_pos_dic = {p.symbol: p.curr_size for p in current_positions}
        for sec in self.ctx.universe:
            if sec not in univ_pos_dic:
                univ_pos_dic[sec] = 0
        return univ_pos_dic

    def util_net_revenue(self, weights_target):
        """
        util = net_revenue = revenue - all costs.
        
        Parameters
        ----------
        weights_target : dict
        
        """
        weights_last = self._get_weights_last()
    
        revenue = self.revenue_model.forecast_revenue(weights_target)
        cost = self.cost_model.calc_cost(weights_last, weights_target)
        # liquid = self.liquid_model.calc_liquid(weight_now)
        risk = self.risk_model.calc_risk(weights_target)
    
        risk_coef = 1.0
        cost_coef = 1.0
        net_revenue = revenue - risk_coef * risk - cost_coef * cost  # - liquid * liq_factor
        return net_revenue
    
    def portfolio_construction(self):
        """
        Calculate target weights of each symbol in the strategy universe.
        User should not modify this function arbitrarily.

        Returns
        -------
        self.weights : weights / GoalPosition (without rounding)
            Weights of each symbol.

        """
        # pick the registered portfolio construction method
        rf = self.func_table[self.pc_method]
        func, options = rf.func, rf.options

        # use the registered method to calculate weights
        weights, msg = func(**options)
        if msg:
            print msg

        # if nan assign zero
        weights = {k: 0.0 if np.isnan(v) else v for k, v in weights.viewitems()}
        
        # step.2 set weights of those not selected to zero
        if self.stock_selector is not None:
            selected_list = self.stock_selector.get_selection()
            weights = {k: v if k in selected_list else 0.0 for k, v in weights.viewitems()}

        # normalize
        w_sum = np.sum(np.abs(weights.values()))
        if w_sum > 1e-8:  # else all zeros weights
            weights = {k: v / w_sum for k, v in weights.viewitems()}

        self.weights = weights

    def equal_weight(self):
        # discrete
        weights = {k: 1.0 for k in self.ctx.universe}
        return weights, ''
    
    def factor_value_weight(self):
        def long_only_weight_adjust(w):
            """
            Adjust weights for long only constraints.
            
            Parameters
            ----------
            w : dict
    
            Returns
            -------
            res : dict
    
            """
            # TODO: we should not add a const
            w_min = np.min(w.values())
            if w_min < 0:
                delta = 2 * abs(w_min)
            # if nan assign zero; else add const
                w = {k: v + delta for k, v in w.viewitems()}
            return w
        
        raw_weights_dic = self.revenue_model.make_forecast()
        weights = {k: 0.0 if np.isnan(v) else v for k, v in raw_weights_dic.viewitems()}
        weights = long_only_weight_adjust(weights)
        return weights, ""
        
    def optimize_mc(self, util_func):
        """
        Use naive search (Monte Carol) to find variable that maximize util_func.
        
        Parameters
        ----------
        util_func : callable
            Input variables, output the value of util function.

        Returns
        -------
        min_weights : dict
            best weights.
        msg : str
            error message.

        """
        n_exp = 5  # number of experiments of Monte Carol
        n_var = len(self.ctx.universe)
    
        weights_mat = np.random.rand(n_exp, n_var)
        weights_mat = weights_mat / weights_mat.sum(axis=1).reshape(-1, 1)
    
        min_f = 1e30
        min_weights = None
        for i in range(n_exp):
            weights = {self.ctx.universe[j]: weights_mat[i, j] for j in range(n_var)}
            f = -util_func(weights)
            if f < min_f:
                min_weights = weights
                min_f = f
    
        if min_weights is None:
            msg = "No weights can make f > {:.2e} found in this search".format(min_f)
        else:
            msg = ""
        return min_weights, msg

    def re_weight_suspension(self, suspensions=None):
        """
        How we deal with weights when there are suspension securities.

        Parameters
        ----------
        suspensions : list of securities
            None if no suspension.

        """
        # TODO this can be refine: consider whether we increase or decrease shares on a suspended symbol.
        if not suspensions:
            return
        
        if len(suspensions) == len(self.ctx.universe):
            raise ValueError("All suspended")  # TODO custom error
        
        weights = {sec: w if w not in suspensions else 0.0 for sec, w in self.weights.viewitems()}
        weights_sum = np.sum(np.abs(weights.values()))
        if weights_sum > 0.0:
            weights = {sec: w / weights_sum for sec, w in weights.viewitems()}
        
        self.weights = weights
    
    def on_after_rebalance(self, total):
        print "\n\n{}, available cash all (exclude suspensions) = {:9.4e}".format(self.ctx.trade_date, total)  # DEBUG
        pass
    
    def send_bullets(self):
        self.goal_portfolio(self.goal_positions)
    
    def generate_weights_order(self, weights_dic, turnover, prices, algo="close", suspensions=None):
        """
        Send order according subject to total turnover and weights of different securities.

        Parameters
        ----------
        weights_dic : dict of {symbol: weight}
            Weight of each symbol.
        turnover : float
            Total turnover goal of all securities. (cash quota)
        prices : dict of {str: float}
            {symbol: price}
        algo : str
            {'close', 'open', 'vwap', etc.}
        suspensions : list of str

        Returns
        -------
        goals : list of GoalPosition
        cash_left : float

        """
        if algo not in ['close', 'vwap']:
            raise NotImplementedError("Currently we only suport order at close price.")
        
        cash_left = 0.0
        cash_used = 0.0
        goals = []
        if algo == 'close' or 'vwap':  # order a certain amount of shares according to current close price
            for sec, w in weights_dic.viewitems():
                goal_pos = GoalPosition()
                goal_pos.symbol = sec
                
                # if algo == 'close':
                # order.price_target = 'close'
                # else:
                # order = VwapOrder()
                # order.symbol = sec
    
                if sec in suspensions:
                    current_pos = self.pm.get_position(sec, self.ctx.trade_date)
                    goal_pos.size = current_pos.curr_size if current_pos is not None else 0
                elif abs(w) < 1e-8:
                    # order.entrust_size = 0
                    goal_pos.size = 0
                else:
                    price = prices[sec]
                    shares_raw = w * turnover / price
                    # shares unit 100
                    shares = int(round(shares_raw / 100., 0))  # TODO cash may be not enough
                    shares_left = shares_raw - shares * 100  # may be negative
                    # cash_left += shares_left * price
                    cash_used += shares * price * 100
                    
                    # order.entrust_size = shares
                    # order.entrust_action = common.ORDER_ACTION.BUY
                    # order.entrust_date = self.trade_date
                    # order.entrust_time = 0
                    # order.order_status = common.ORDER_STATUS.NEW
                    goal_pos.size = shares
                
                # orders.append(order)
                goals.append(goal_pos)
        
        cash_left = turnover - cash_used
        return goals, cash_left
    
    def liquidate_all(self):
        for sec in self.pm.holding_securities:
            curr_size = self.pm.get_position(sec, self.ctx.trade_date).curr_size
            self.place_order(sec, common.ORDER_ACTION.SELL, 1e-3, curr_size)
    
    def query_portfolio(self):
        positions = []
        for sec in self.pm.holding_securities:
            positions.append(self.pm.get_position(sec, self.ctx.trade_date))
        return positions


class EventDrivenStrategy(Strategy, Subscriber):
    def __init__(self):
        
        Strategy.__init__(self)
        
        self.pm = PortfolioManager()
        self.pm.strategy = self
        
        # TODO remove
        self.eventEngine = EventEngine()
        self.eventEngine.register(eventType.EVENT_TIMER, self.on_cycle)
        self.eventEngine.register(eventType.EVENT_MD_QUOTE, self.on_quote)
        self.eventEngine.register(eventType.EVENT_TRADE_IND, self.pm.on_trade_ind)
        self.eventEngine.register(eventType.EVENT_ORDERSTATUS_IND, self.pm.on_order_status)
    
    @abstractmethod
    def on_new_day(self, trade_date):
        pass
    
    @abstractmethod
    def on_quote(self, quote):
        pass
    
    @abstractmethod
    def on_cycle(self):
        pass
    
    def initialize(self, runmode):
        if runmode == common.RUN_MODE.REALTIME:
            self.subscribe_events()
    
    def subscribe_events(self):
        universe = self.ctx.universe
        data_server = self.ctx.dataserver
        for i in xrange(len(universe)):
            self.subscribe(data_server, universe[i])
    
    def subscribe(self, publisher, topic):
        publisher.add_subscriber(self, topic)
    
    def start(self):
        self.eventEngine.start(False)
    
    def stop(self):
        self.eventEngine.stop()
    
    def register_event(self, event):
        self.eventEngine.put(event)
