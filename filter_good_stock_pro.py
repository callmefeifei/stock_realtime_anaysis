#!coding:utf-8

# 依赖库
import os
import sys
import time
import datetime
import json
import requests
import threading
import ConfigParser

# 协程
import gevent
from gevent import monkey; monkey.patch_all()
from gevent.pool import Pool

reload(sys)
sys.setdefaultencoding("utf-8")

"""                         命令行筛选
# 按今日净流入排序
cat result/20210121_rule4.txt |sort -t $':' -k4 -nr

# 按昨日净流入排序
cat result/20210121_rule4.txt |sort -t $':' -k2 -nr
s
# 按昨日涨跌幅排序
cat result/20210121_rule4.txt |sort -t $':' -k3 -nr

# 按今日涨跌幅排序
cat result/20210121_rule4.txt |sort -t $':' -k5 -nr

# 按近五日涨跌幅排序
cat result/20210121_rule4.txt |sort -t $':' -k7 -nr
"""

"""
:TODO
1. 增加异动股票涨停监测
2. 增加新策略.
3. 增加一个当天发现的缓存列表...避免再次打开丢失之前的.
"""
"""
近五日资金流入>0, top 100
"""

class StockNet():
    def __init__(self, token=None, is_limit=False, limit_num=100):
        # 告警token
        self.wx_token = token

        # 是否限制数量
        self.is_limit = is_limit

        # 限制数量
        self.limit_num = limit_num

        self.net_money_flow = []

        # 上个交易日股票数据列表
        self.yestoday_stock_dict = {}
        self.yestoday_stock_list = []

        # 最后交易日股票数据列表
        self.now_stock_dict = {}

        # 线程池数量
        self.pool_num = 200

        # 所有股票代码
        self.all_stock_code = {}

        # 所有均线数据
        self.stock_jx_data = {}

        # 获取交易日期
        self.get_trade_date()

        # 计数
        self.count = 0

        # 获取均线数据状态
        self._jx_data_count = 0
        self.jx_data_status = 0
        self.jx_data_count = 0
        # 昨日收盘数据状态
        self.ys_data_status = 0
        self.ys_data_count = 0
        # 当日收盘数据状态
        self.now_data_status = 0
        self.now_data_count = 0

        # 筛选出的股票代码列表
        self.rule_matched_list = {}
        self.rule_matched_list['rule1'] = []
        self.rule_matched_list['rule2'] = []
        self.rule_matched_list['rule3'] = []
        self.rule_matched_list['rule4'] = []
        self.rule_matched_list['rule5'] = []

        # 告警去重
        self.alarm_db = {}

        # 交易结束信号
        self.close_signal = False

    def notify(self, s_title, s_content):
        try:
            s_title = "股票异动提醒"
            api_url = 'https://api.ossec.cn/v1/send?token=%s' % self.wx_token
            api_url += '&topic=%s&message=%s' % (s_title, s_content)
            response  = requests.get(api_url, timeout=60)
        except:
            pass

    # 结果保存
    def write_result(self, rule, content):
        filename = time.strftime('%Y%m%d' , time.localtime())+"_"+rule+".txt"
        if not os.path.exists("./result/"):
            os.makedirs("./result/")

        with open("./result/"+filename, "a+") as f:
            f.write(content+"\n")

    # 获取数据进度监控
    def status_monitor(self):
        while True:
            # 获取均线状态监控
            if self.jx_data_status == 0:
                jx_data_status = '待开始'
            elif self.jx_data_status == 1:
                jx_data_status = '获取中'
            elif self.jx_data_status == 2:
                jx_data_status = '获取完毕'

            # 获取昨日交易数据状态监控
            if self.ys_data_status == 0:
                ys_data_status = '待开始'
            elif self.ys_data_status == 1:
                ys_data_status = '获取中'
            elif self.ys_data_status == 2:
                ys_data_status = '获取完毕'

            # 获取当日交易数据状态监控
            if self.now_data_status == 0:
                now_data_status = '待开始'
            elif self.now_data_status == 1:
                now_data_status = '获取中'
            elif self.ys_data_status == 2:
                now_data_status = '获取完毕'

            if self.jx_data_status != 0 and self.jx_data_status != 3:
                print("[*] 均线数据获取任务状态 %s , 剩余数量:%s" % (jx_data_status, len(self.all_stock_code.keys())-self.jx_data_count))
                if self.jx_data_status == 2:
                    self.jx_data_status = 3

            if self.ys_data_status != 0 and self.ys_data_status != 3:
                print("[*] 昨日交易数据获取任务状态 %s , 剩余数量:%s" % (ys_data_status, len(self.stock_jx_data.keys())-self.ys_data_count))
                if self.ys_data_status == 2:
                    self.ys_data_status = 3

            if self.now_data_status != 0 and self.now_data_status != 0:
                print("[*] 当日交易数据获取任务状态 %s , 剩余数量:%s" % (now_data_status, len(self.stock_jx_data.keys())-self.now_data_count))
                if self.now_data_status == 2:
                    self.now_data_status = 3

            # 如果都结束, 则退出
            if self.jx_data_status == 3 and self.ys_data_status == 3 and self.now_data_status == 3:
                break

            if self.close_signal:
                break

            time.sleep(5)

    # 获取现价以及涨跌幅
    def fetch_now_changepercent(self, secid, code):
        try:
            url = "http://push2.eastmoney.com/api/qt/stock/get?cb=&fltt=2&invt=2&secid=%s.%s&fields=f43,f170" % (secid, code)
            con = requests.get(url, timeout=3).json()
            trade = con['data']['f43']
            now_changepercent = con['data']['f170']
        except:
            trade = 0
            now_changepercent = 0

        return trade, now_changepercent

    # 获取资金流量方法
    def fetch_money_flow(self, code, rule_type):
        try:
            if code.startswith("300") or code.startswith("00"):
                secid = 0
            elif code.startswith("68"):
                pass
            else:
                secid = 1

            url = "http://push2.eastmoney.com/api/qt/stock/fflow/kline/get?lmt=2&klt=1&secid=%s.%s&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63&ut=" % (secid, code)
            data = requests.get(url, timeout=5).json()['data']
            klines = data['klines']
            name = data['name']

            # 现价
            #now_trade, now_changepercent = self.fetch_now_changepercent(secid, code)
            try:
                url = "http://push2.eastmoney.com/api/qt/stock/get?cb=&fltt=2&invt=2&secid=%s.%s&fields=f43,f170" % (secid, code)
                con = requests.get(url, timeout=5).json()
                now_trade = con['data']['f43']
                now_changepercent = con['data']['f170']
            except:
                now_trade = 0
                now_changepercent = 0

            money_flow_bs = 1.0

            # 上分钟时间数据
            in_money_1 = float(klines[0].split(",")[1])

            # 最后交易时间数据
            in_money_2 = float(klines[1].split(",")[1])

            # 都为净流出
            if float(in_money_2) < 0 and float(in_money_1) < 0:
                # 流入
                if float(in_money_2) > float(in_money_1):
                    money_flow_bs = float(in_money_1) / float(in_money_2)
                # 流出
                else:
                    money_flow_bs = float(in_money_2) / float(in_money_1) / -1

            # 都为净流入
            if float(in_money_2) > 0 and float(in_money_1) > 0:
                if float(in_money_2) > float(in_money_1):
                    money_flow_bs = float(in_money_2) / float(in_money_1)
                # 流出
                else:
                    money_flow_bs = float(in_money_1) / float(in_money_2) / -1

            # 最近净流入, 上次净流出
            if float(in_money_2) > 0 and float(in_money_1) < 0:
                if abs(float(in_money_2)) > abs(float(in_money_1)):
                    money_flow_bs = float(in_money_2) / float(in_money_1) / -1
                else:
                    money_flow_bs = float(in_money_1) / float(in_money_2) / -1

            # 最近净流出, 上次净流入
            if float(in_money_2) < 0 and float(in_money_1) > 0:
                if abs(float(in_money_2)) < abs(float(in_money_1)):
                    money_flow_bs = float(in_money_1) / float(in_money_2)
                else:
                    money_flow_bs = float(in_money_2) / float(in_money_1)

            min1flow = (float(in_money_2) - float(in_money_1)) / 10000

            print "[*][%s][%s][%s][%s] 现价:%s 涨跌幅:%s 当前资金净流入:%s万 近一分钟净流入:%s万 与上分钟比资金流入倍数:%s" % (time.strftime('%Y-%m-%d %H:%M:%S' , time.localtime()), rule_type, code, name, now_trade, now_changepercent, in_money_2/10000, min1flow, money_flow_bs)

            # 资金流入倍数>2, 则认为异动. 资金流入倍数 -x, 跌
            is_matched = False
            if money_flow_bs <= -2:
                note = "出现大幅流出."
                is_matched = True

            elif money_flow_bs >= 2:
                note = "出现大幅流入."
                is_matched = True

            if is_matched:
                content = "发现股票存在异动, 股票代码: %s[%s][%s][%s][%s万] | 命中规则: %s | 信号: %s | 与上分钟比资金倍数: %s"  % (code, name, now_trade, now_changepercent, in_money_2/10000, rule_type, note, money_flow_bs)
                _content = "[*][%s][%s][%s][现价:%s][涨跌幅:%s][净流入:%s万] 发现异动 | 股票代码: %s | 命中规则: %s | 信号: %s | 与上分钟比资金倍数: %s"  % (time.strftime('%Y-%m-%d %H:%M:%S' , time.localtime()), code, name, now_trade, now_changepercent, in_money_2/10000, code, rule_type, note, money_flow_bs)

                # 判断是否已告警过
                if code not in self.alarm_db.keys():
                    if '大幅流出' not in note:
                        self.notify("发现异动股票", content)
                    self.alarm_db[code] = {in_money_2:True}
                else:
                    if in_money_2 in self.alarm_db[code].keys():
                        pass
                    else:
                        if '大幅流出' not in note:
                            self.notify("发现异动股票", content)
                        self.alarm_db[code] = {in_money_2:True}

                # 记录结果到本地
                self.write_result("money_flow", _content)

        except Exception as e:
            if "float division by zero" in str(e):
                pass
            elif "nodename nor servname provided, or not known" in str(e):
                pass
            else:
                print("[-] 获取获取资金流量方法失败!! errcode:100205, errmsg:%s" % e)

    def monitor_money_flow(self):
        # 循环监控
        while True:
            try:
                # rule1
                rule1_list = self.rule_matched_list['rule1']
            
                # rule2
                rule2_list = self.rule_matched_list['rule2']

                # rule3
                rule3_list = self.rule_matched_list['rule3']

                # rule4
                rule4_list = self.rule_matched_list['rule4']

                # rule5
                rule5_list = self.rule_matched_list['rule5']


                p = Pool(100)
                threads = []
                # rule1
                for code in rule1_list:
                    threads.append(p.spawn(self.fetch_money_flow, code, "rule1"))

                # rule2
                for code in rule2_list:
                    threads.append(p.spawn(self.fetch_money_flow, code, "rule2"))

                # rule3
                for code in rule2_list:
                    threads.append(p.spawn(self.fetch_money_flow, code, "rule3"))

                # rule4
                for code in rule4_list:
                    threads.append(p.spawn(self.fetch_money_flow, code, "rule4"))

                # rule5
                for code in rule5_list:
                    threads.append(p.spawn(self.fetch_money_flow, code, "rule5"))

                gevent.joinall(threads)

                time.sleep(5)

                if self.close_signal:
                    break
            except Exception as e:
                print("[-] 获取昨日股票收盘数据失败!! errcode:100204, errmsg:%s" % e)
                time.sleep(3)
                continue

    # 检查股票所属板块
    def check_stock_plat(self, code):
        # 股票板面
        stock_code = code
        if stock_code.startswith('300'):
            stock_plat = u'创业板面'
            stock_prefix = 'sz'

        elif stock_code.startswith('600') or stock_code.startswith('601') or stock_code.startswith('603') or stock_code.startswith('605'):
            stock_plat = u'沪市A股'
            stock_prefix = 'sh'

        elif stock_code.startswith('900'):
            stock_plat = u'沪市B股'
            stock_prefix = 'sh'

        elif stock_code.startswith('000'):
            stock_plat = u'深市A股'
            stock_prefix = 'sz'

        elif stock_code.startswith('001'):
            stock_plat = u'深市A股'
            stock_prefix = 'sz'

        elif stock_code.startswith('002'):
            stock_plat = u'中小板面'
            stock_prefix = 'sz'

        elif stock_code.startswith('003'):
            stock_plat = u'深市A股'
            stock_prefix = 'sz'

        elif stock_code.startswith('200'):
            stock_plat = u'深市B股'
            stock_prefix = 'sz'

        elif stock_code.startswith('688') or stock_code.startswith('689'):
            stock_plat = u'科创板面'
            stock_prefix = 'sh'

        else:
            stock_plat = u'其他'
            try:
                res = requests.get("https://suggest3.sinajs.cn/suggest/type=11,12,13,14,15,72&key=%s" % code , timeout=3).text.split('"')[1]
                if res:
                    stock_prefix = res.split(',')[0][:2]
                else:
                    stock_prefix = 'es'
            except:
                stock_prefix = 'es'

        self.all_stock_code[code] = {"code":code, "ts_code":stock_prefix+code, "platform":stock_plat}

        return stock_plat, stock_prefix

    def get_all_url(self, flag):
        url_list = []

        #all_code = self.all_stock_code.keys()
        all_code = self.stock_jx_data.keys()
        while True:
            if all_code:
                code = str(all_code.pop())
                if code.startswith("300") or code.startswith("00"):
                    secid = 0
                elif code.startswith("68"):
                    continue
                else:
                    secid = 1

                if flag == 0: # 上个交易日
                    url = None
                    # 交易时间
                    if int(time.strftime('%H' , time.localtime())) >= 9 and int(time.strftime('%H' , time.localtime())) < 15:
                        if int(time.strftime('%M' , time.localtime())) > 30:
                            url = "http://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?cb=&lmt=5&klt=101&fields1=f2,f3,f7&fields2=f52,f63&ut=&secid=%s.%s" % (secid, code)

                    # 非交易时间
                    if url is None:url = "http://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?cb=&lmt=6&klt=101&fields1=f2,f3,f7&fields2=f52,f63&ut=&secid=%s.%s" % (secid, code)

                elif flag == 1: # 当前交易日:涨幅
                    url = "http://push2.eastmoney.com/api/qt/stock/get?cb=&fltt=2&invt=2&secid=%s.%s&fields=f170&ut=&_=1610344699504" % (secid, code)
                elif flag == 2: # 当前交易日:净流入
                    url = "http://push2.eastmoney.com/api/qt/stock/fflow/kline/get?lmt=1&klt=1&secid=%s.%s&fields1=f1,f2,f3,f7&fields2=f52&ut=&cb=&_=" % (secid, code)

                url_list.append([code, url, self.all_stock_code[code]['ts_code']])
            else:
                break

        return url_list


    # 获取当前交易日 & 上个交易日的日期
    def get_trade_date(self):
        is_trade = False
        if int(time.strftime('%H' , time.localtime())) >= 9 and int(time.strftime('%H' , time.localtime())) < 15:
            if int(time.strftime('%H' , time.localtime())) == 9:
                if int(time.strftime('%M' , time.localtime())) > 30:
                    is_trade = True
            else:
                is_trade = True

        # 如果不是周六、周日则是交易日
        if datetime.datetime.now().isoweekday() > 5:
            is_trade = False

        # 通过API获取交易时间信息
        try:
            all_trade_time = [i[0] for i in requests.get("http://api.finance.ifeng.com/akdaily/?code=sz002307&type=last", timeout=3).json()['record']]
        except:
            all_trade_time = []

        if len(all_trade_time) <= 0:
            print("[-] 获取交易时间失败!! errcode:100202")
            sys.exit()

        # 交易时间
        if is_trade:
            self.now_date = time.strftime('%Y-%m-%d' , time.localtime())
            self.last_date = all_trade_time[-1]

        # 非交易时间
        else:
            self.now_date = all_trade_time[-1]
            self.last_date = all_trade_time[-2]

        return self.last_date, self.now_date

    # 获取全量股票代码以及前缀
    def get_all_code(self):
        try:
            stock_code_list = [ i['f12'] for i in requests.get("http://21.push2.eastmoney.com/api/qt/clist/get?cb=&pn=1&pz=10000&po=1&np=1&ut=&fltt=2&invt=2&fid=f3&fs=m:0+t:6,m:0+t:13,m:0+t:80,m:1+t:2,m:1+t:23&fields=f2,f3,f12,f14", timeout=3).json()['data']['diff'] ]
        except:
            stock_code_list = []

        if stock_code_list > 0:
            p = Pool(200)
            threads = []
            for code in stock_code_list:
                threads.append(p.spawn(self.check_stock_plat, code))
            gevent.joinall(threads)
        else:
            print("[-] 获取股票列表失败!! errcode:100201")
            sys.exit()

    # 获取历史数据
    def get_his_data(self, code):
        try:
            if code in self.stock_jx_data.keys():
                his_stock_data = [ i for i in self.stock_jx_data[code] if i['date'] == self.last_date][0]
                ma5 = his_stock_data['ma5']
                ma10 = his_stock_data['ma10']
                ma30 = his_stock_data['ma20']
                trade = his_stock_data['close']
            else:
                ma5 = 0
                ma10 = 0
                ma30 = 0
                trade = 0
        except:
            ma5 = 0
            ma10 = 0
            ma30 = 0
            trade = 0
        
        return ma5, ma10, ma30, trade

    # 获取昨天收盘数据方法
    def yestody_data_func(self, code, url, ts_code):
        """
        # 获取当前均线价格
        try:
            ma_api = "https://quotes.sina.cn/cn/api/jsonp_v2.php/=/CN_MarketDataService.getKLineData?symbol=%s&scale=240&datalen=1" % (ts_code)
            con = requests.get(url=ma_api, timeout=10).text
            con = json.loads(con.split("[")[1][:-3])
            ma5 = float(con['ma_price5'])
            ma10 = float(con['ma_price10'])
            ma30 = float(con['ma_price30'])
            trade = float(con['close'])
        except Exception as e:
            ma5 = 0
            ma10 = 0
            ma30 = 0
            trade = 0
        """

        # 获取当前均线价格
        try:
            ma_api = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData?symbol=%s&scale=240&datalen=1" % (ts_code)
            con = requests.get(url=ma_api, timeout=10).json()[0]
            ma5 = float(con['ma_price5'])
            ma10 = float(con['ma_price10'])
            ma30 = float(con['ma_price30'])
            trade = float(con['close'])
        except Exception as e:
            ma5 = 0
            ma10 = 0
            ma30 = 0
            trade = 0

        # 获取昨日均线价格
        y_ma5, y_ma10, y_ma30,y_trade = self.get_his_data(code)

        # 获取昨日资金详情
        try:
            con = requests.get(url, timeout=10).json()

            name = con['data']['name']

            # 交易时间
            is_trade = False
            if int(time.strftime('%H' , time.localtime())) >= 9 and int(time.strftime('%H' , time.localtime())) < 15:
                if int(time.strftime('%H' , time.localtime())) == 9:
                    if int(time.strftime('%M' , time.localtime())) > 30:
                        jlr = float(con['data']['klines'][-1].split(",")[0])/10000
                        zdf = float(con['data']['klines'][-1].split(",")[1])
                        jlr_5days = sum([float(i.split(",")[0]) for i in con['data']['klines'][1:]])/10000
                        zdf_5days = sum([float(i.split(",")[1]) for i in con['data']['klines'][1:]])
                        is_trade = True
                else:
                    jlr = float(con['data']['klines'][-1].split(",")[0])/10000
                    zdf = float(con['data']['klines'][-1].split(",")[1])
                    jlr_5days = sum([float(i.split(",")[0]) for i in con['data']['klines'][1:]])/10000
                    zdf_5days = sum([float(i.split(",")[1]) for i in con['data']['klines'][1:]])
                    is_trade = True

            # 非交易时间
            if is_trade is False:
                # 净流入
                jlr = float(con['data']['klines'][-2].split(",")[0])/10000
                zdf = float(con['data']['klines'][-2].split(",")[1])
                jlr_5days = sum([float(i.split(",")[0]) for i in con['data']['klines'][-6:][:5]])/10000
                zdf_5days = sum([float(i.split(",")[1]) for i in con['data']['klines'][-6:][:5]])

                stock_info_list = [ i.split(",") for i in con['data']['klines'][:-1]]
            else:
                stock_info_list = [ i.split(",") for i in con['data']['klines']]

            if code not in self.yestoday_stock_dict.keys():
                self.yestoday_stock_dict[code] = {}
                self.yestoday_stock_dict[code]['code'] = code
                self.yestoday_stock_dict[code]['name'] = name
                self.yestoday_stock_dict[code]['jlr'] = jlr
                self.yestoday_stock_dict[code]['zdf'] = zdf
                self.yestoday_stock_dict[code]['jlr_5days'] = jlr_5days
                self.yestoday_stock_dict[code]['zdf_5days'] = zdf_5days
                self.yestoday_stock_dict[code]['ma5'] = ma5
                self.yestoday_stock_dict[code]['ma10'] = ma10
                self.yestoday_stock_dict[code]['ma30'] = ma30
                self.yestoday_stock_dict[code]['trade'] = trade
                self.yestoday_stock_dict[code]['y_ma5'] = y_ma5
                self.yestoday_stock_dict[code]['y_ma10'] = y_ma10
                self.yestoday_stock_dict[code]['y_ma30'] = y_ma30
                self.yestoday_stock_dict[code]['y_trade'] = y_trade
                self.yestoday_stock_dict[code]['stock_info_list'] = stock_info_list
            else:
                self.yestoday_stock_dict[code]['code'] = code
                self.yestoday_stock_dict[code]['name'] = name
                self.yestoday_stock_dict[code]['jlr'] = jlr
                self.yestoday_stock_dict[code]['zdf'] = zdf
                self.yestoday_stock_dict[code]['jlr_5days'] = jlr_5days
                self.yestoday_stock_dict[code]['zdf_5days'] = zdf_5days
                self.yestoday_stock_dict[code]['ma5'] = ma5
                self.yestoday_stock_dict[code]['ma10'] = ma10
                self.yestoday_stock_dict[code]['ma30'] = ma30
                self.yestoday_stock_dict[code]['trade'] = trade
                self.yestoday_stock_dict[code]['y_ma5'] = y_ma5
                self.yestoday_stock_dict[code]['y_ma10'] = y_ma10
                self.yestoday_stock_dict[code]['y_ma30'] = y_ma30
                self.yestoday_stock_dict[code]['y_trade'] = y_trade
                self.yestoday_stock_dict[code]['stock_info_list'] = stock_info_list
        except Exception as e:
            pass

        self.ys_data_count += 1

    # 获取昨天收盘数据
    def get_yestody_stock(self):
        try:
            all_url = self.get_all_url(flag=0)

            p = Pool(300)
            threads = []
            # 获取昨天收盘数据开始
            self.ys_data_status = 1
            for u in all_url:
                try:
                    code = u[0]
                    url = u[1]
                    ts_code = u[2]
                    threads.append(p.spawn(self.yestody_data_func, code, url, ts_code))
                except Exception as e:
                    continue

            gevent.joinall(threads)

            # 生成昨日股票数据列表
            self.yestoday_stock_list = []
            for i in self.yestoday_stock_dict:
                self.yestoday_stock_list.append(self.yestoday_stock_dict[i])

        except Exception as e:
            print("[-] 获取昨日股票收盘数据失败!! errcode:100204, errmsg:%s" % e)
            return

    # 获取当前股票数据方法
    def now_data_func(self, code, url, ts_code):
        try:
            # 涨跌幅
            if code.startswith("300") or code.startswith("00"):
                secid = 0
            else:
                secid = 1

            zdf_url = "http://push2.eastmoney.com/api/qt/stock/get?cb=&fltt=2&invt=2&secid=%s.%s&fields=f170&ut=&_=1610344699504" % (secid, code)
            con = requests.get(zdf_url, timeout=10).json()
            zdf = con['data']['f170']

            # 净流入
            con = requests.get(url, timeout=10).json()
            name = con['data']['name']
            jlr = float(con['data']['klines'][0])/10000
            if code not in self.now_stock_dict.keys():
                self.now_stock_dict[code] = {}
                self.now_stock_dict[code]['code'] = code
                self.now_stock_dict[code]['name'] = name
                self.now_stock_dict[code]['jlr'] = jlr
                self.now_stock_dict[code]['zdf'] = zdf
            else:
                self.now_stock_dict[code]['code'] = code
                self.now_stock_dict[code]['name'] = name
                self.now_stock_dict[code]['jlr'] = jlr
                self.now_stock_dict[code]['zdf'] = zdf

        except Exception as e:
            #print(url, e)
            pass

        self.now_data_count += 1

    # 获取当日收盘数据
    def get_now_stock(self):
        try:
            all_url = self.get_all_url(flag=2)

            p = Pool(300)
            threads = []
            # 获取当日收盘数据开始
            self.now_data_status = 1
            for u in all_url:
                code = u[0]
                url = u[1]
                ts_code = u[2]
                threads.append(p.spawn(self.now_data_func, code, url, ts_code))

            gevent.joinall(threads)

        except Exception as e:
            print("[-] 获取昨日股票收盘数据失败!! errcode:100204, errmsg:%s" % e)
            sys.exit()


    # 均线数据获取赋值
    def jx_data_func(self, url, code):
        try:
            jx_data = requests.get(url, timeout=5).json()['record']
        except:
            jx_data = []

        for stock in jx_data:
            try:
                stock_dict = {}
                stock_dict['date'] = stock[0]
                stock_dict['open'] = stock[1]
                stock_dict['high'] = stock[2]
                stock_dict['close'] = stock[3]
                stock_dict['low'] = stock[4]
                stock_dict['volume'] = stock[5]
                stock_dict['changepercent'] = stock[7]
                stock_dict['ma5'] = stock[8]
                stock_dict['ma10'] = stock[9]
                stock_dict['ma20'] = stock[10]
                stock_dict['hsl'] = stock[14]

                if code in self.stock_jx_data.keys():
                    self.stock_jx_data[code].append(stock_dict)
                else:
                    self.stock_jx_data[code] = []
                    self.stock_jx_data[code].append(stock_dict)
            except Exception as e:
                continue

        self.jx_data_count += 1

    # 通过规则筛选需要股票
    def rule_filter(self):
        # 昨日净流入且大涨
        # 今日净流出且大跌


        # rule5: 近五日净流入大 & 近五日涨跌幅小
        # 1. 近五日资金净流入 > 0 且 资金净流入排名靠前(top100?)
        # 2. 近五日涨跌幅 <= 1
        try:
            rule5_list = []
            # 首先筛选资金净流入>0 的 且近5天资金净流入排名top100的
            for i in sorted(self.yestoday_stock_list, key=lambda x:x['jlr_5days'], reverse=True):
                if len(rule5_list) >= 100:
                    break
                else:
                    if i['jlr_5days'] > 0:
                        rule5_list.append(i)
                    else:
                        continue

            # 筛选近五日涨跌幅 <= 1的
            for i in rule5_list:
                try:
                    if i['zdf_5days'] <= 1.5:
                        code = i['code']
                        stock = code
                        name = self.yestoday_stock_dict[code]['name']
                        zdf = self.yestoday_stock_dict[code]['zdf']
                        jlr = self.yestoday_stock_dict[code]['jlr']
                        content = "[+][%s][rule5][%s][%s] 昨日净流入:%s 昨日涨跌幅:%s 今日净流入:%s 今日涨跌幅:%s 近五日净流入:%s万 近五日涨跌幅:%s ma5:%s ma10:%s ma30:%s" % (time.strftime('%Y-%m-%d %H:%M:%S' , time.localtime()), code, self.yestoday_stock_dict[stock]['name'], jlr, zdf, self.now_stock_dict[stock]['jlr'], self.now_stock_dict[stock]['zdf'], self.yestoday_stock_dict[stock]['jlr_5days'], self.yestoday_stock_dict[stock]['zdf_5days'], self.yestoday_stock_dict[stock]['ma5'], self.yestoday_stock_dict[stock]['ma10'], self.yestoday_stock_dict[stock]['ma30'])
                        if code not in self.rule_matched_list['rule5']:
                            self.rule_matched_list['rule5'].append(code)
                        self.write_result("rule5", content)
                    else:
                        continue
                except Exception as e1:
                    continue
        except Exception as e2:
            pass

        # 其他规则
        for stock in self.yestoday_stock_dict:
            try:
                code = self.yestoday_stock_dict[stock]['code']
                name = self.yestoday_stock_dict[stock]['name']
                zdf = self.yestoday_stock_dict[stock]['zdf']
                jlr = self.yestoday_stock_dict[stock]['jlr']

                # 昨日净流入>1000w, 且涨跌幅>3
                #print "[-][%s][%s] 昨日净流入:%s 昨日涨跌幅:%s 今日净流入:%s 今日涨跌幅:%s 近五日净流入:%s" % (code, self.yestoday_stock_dict[stock]['name'], jlr, zdf, self.now_stock_dict[stock]['jlr'], self.now_stock_dict[stock]['zdf'], self.yestoday_stock_dict[stock]['jlr_5days'])

                # rule1:昨日净流出，今日净流出
                if jlr > 1000 and zdf >= 3:
                    # 今日净流出 < -1000
                    if self.now_stock_dict[stock]['jlr'] <= -1000 and self.now_stock_dict[stock]['zdf'] <= -4:
                        content = "[+][%s][rule1][%s][%s][%s] 昨日净流入:%s 昨日涨跌幅:%s 今日净流入:%s 今日涨跌幅:%s 近五日净流入:%s万 近五日涨跌幅:%s ma5:%s ma10:%s ma30:%s" % (time.strftime('%Y-%m-%d %H:%M:%S' , time.localtime()), code, self.yestoday_stock_dict[stock]['name'], jlr, zdf, self.now_stock_dict[stock]['jlr'], self.now_stock_dict[stock]['zdf'], self.yestoday_stock_dict[stock]['jlr_5days'], self.yestoday_stock_dict[stock]['zdf_5days'], self.yestoday_stock_dict[stock]['ma5'], self.yestoday_stock_dict[stock]['ma10'], self.yestoday_stock_dict[stock]['ma30'])
                        #print content
                        if code not in self.rule_matched_list['rule1']:
                            self.rule_matched_list['rule1'].append(code)

                        self.write_result("rule1", content)

                # rule2:昨日净流出, 今日净流出.
                if jlr < 0 and zdf <= 0:
                    if self.now_stock_dict[stock]['jlr'] <= 0 and self.now_stock_dict[stock]['zdf'] <= 0:
                        # 小于5日线，大于30日线
                        if self.yestoday_stock_dict[stock]['trade'] < self.yestoday_stock_dict[stock]['ma5'] and self.yestoday_stock_dict[stock]['trade'] > self.yestoday_stock_dict[stock]['ma30']:
                            content = "[+][%s][rule2][%s][%s] 昨日净流入:%s 昨日涨跌幅:%s 今日净流入:%s 今日涨跌幅:%s 近五日净流入:%s万 近五日涨跌幅:%s ma5:%s ma10:%s ma30:%s" % (time.strftime('%Y-%m-%d %H:%M:%S' , time.localtime()), code, self.yestoday_stock_dict[stock]['name'], jlr, zdf, self.now_stock_dict[stock]['jlr'], self.now_stock_dict[stock]['zdf'], self.yestoday_stock_dict[stock]['jlr_5days'], self.yestoday_stock_dict[stock]['zdf_5days'], self.yestoday_stock_dict[stock]['ma5'], self.yestoday_stock_dict[stock]['ma10'], self.yestoday_stock_dict[stock]['ma30'])
                            #print content
                            if code not in self.rule_matched_list['rule2']:
                                self.rule_matched_list['rule2'].append(code)

                            self.write_result("rule2", content)

                # rule3:今日首次净流入且涨, 前两天均净流出且跌.
                if self.now_stock_dict[stock]['jlr'] > 0 and self.now_stock_dict[stock]['zdf'] > 0 \
                    and float(self.yestoday_stock_dict[stock]['stock_info_list'][-1][0]) < 0 \
                    and float(self.yestoday_stock_dict[stock]['stock_info_list'][-2][0]) < 0 \
                    and float(self.yestoday_stock_dict[stock]['stock_info_list'][-1][1]) < 0 \
                    and float(self.yestoday_stock_dict[stock]['stock_info_list'][-2][1]) < 0 \
                    and self.yestoday_stock_dict[stock]['zdf_5days'] < 0 \
                    and self.yestoday_stock_dict[stock]['trade'] < self.yestoday_stock_dict[stock]['ma5'] and self.yestoday_stock_dict[stock]['trade'] > self.yestoday_stock_dict[stock]['ma30'] \
                    and 1==1:
                    content = "[+][%s][rule3][%s][%s] 昨日净流入:%s 昨日涨跌幅:%s 今日净流入:%s 今日涨跌幅:%s 近五日净流入:%s万 近五日涨跌幅:%s ma5:%s ma10:%s ma30:%s" % (time.strftime('%Y-%m-%d %H:%M:%S' , time.localtime()), code, self.yestoday_stock_dict[stock]['name'], jlr, zdf, self.now_stock_dict[stock]['jlr'], self.now_stock_dict[stock]['zdf'], self.yestoday_stock_dict[stock]['jlr_5days'], self.yestoday_stock_dict[stock]['zdf_5days'], self.yestoday_stock_dict[stock]['ma5'], self.yestoday_stock_dict[stock]['ma10'], self.yestoday_stock_dict[stock]['ma30'])
                    #print content
                    if code not in self.rule_matched_list['rule3']:
                        self.rule_matched_list['rule3'].append(code)

                    self.write_result("rule3", content)

                #import pdb;pdb.set_trace()
                # rule4:刚突破ma5->ma10<ma20
                # 1. 当前涨跌幅>0
                # 2. 昨日收盘价>ma5 and 昨日收盘价<ma10
                # 3. 现价>ma5 and 现价>ma10 and 现价 < ma30
                if self.now_stock_dict[stock]['zdf'] > 0 \
                    and self.yestoday_stock_dict[stock]['y_trade'] > self.yestoday_stock_dict[stock]['y_ma5'] \
                    and self.yestoday_stock_dict[stock]['y_trade'] < self.yestoday_stock_dict[stock]['y_ma10'] \
                    and self.yestoday_stock_dict[stock]['trade'] > self.yestoday_stock_dict[stock]['ma5'] \
                    and self.yestoday_stock_dict[stock]['trade'] < self.yestoday_stock_dict[stock]['ma30'] \
                    and self.yestoday_stock_dict[stock]['ma30'] > self.yestoday_stock_dict[stock]['ma10'] \
                    and self.yestoday_stock_dict[stock]['ma10'] > self.yestoday_stock_dict[stock]['ma5'] \
                    and 1==1:
                    content = "[+][%s][rule4][%s][%s] 昨日净流入:%s 昨日涨跌幅:%s 今日净流入:%s 今日涨跌幅:%s 近五日净流入:%s万 近五日涨跌幅:%s ma5:%s ma10:%s ma30:%s" % (time.strftime('%Y-%m-%d %H:%M:%S' , time.localtime()), code, self.yestoday_stock_dict[stock]['name'], jlr, zdf, self.now_stock_dict[stock]['jlr'], self.now_stock_dict[stock]['zdf'], self.yestoday_stock_dict[stock]['jlr_5days'], self.yestoday_stock_dict[stock]['zdf_5days'], self.yestoday_stock_dict[stock]['ma5'], self.yestoday_stock_dict[stock]['ma10'], self.yestoday_stock_dict[stock]['ma30'])
                    #print content
                    if code not in self.rule_matched_list['rule4']:
                        self.rule_matched_list['rule4'].append(code)
                    self.write_result("rule4", content)
            except Exception as e:
                pass

    # 获取所有股票均线数据
    def get_jx_data(self):
        try:

            # 先判断是否有当日本地缓存, 如果有直接加载
            jx_cache_file="./cache/%s.json" % str(time.strftime('%Y%m%d' , time.localtime()))
            if not os.path.exists("./cache"):
                os.makedirs("./cache/")

            if os.path.exists(jx_cache_file):
                with open(jx_cache_file, 'r') as f:
                    cache = f.read()

                print("[+] 发现当天缓存文件, 加载中请稍后...")
                self.stock_jx_data = json.loads(cache)
                if self.is_limit:
                    count = 0
                    _stock_jx_data = {}
                    for i in self.stock_jx_data:
                        count += 1
                        if count >= self.limit_num:
                            break

                        _stock_jx_data[i] = self.stock_jx_data[i]

                    self.stock_jx_data = _stock_jx_data

            else:
                p = Pool(300)
                threads = []
                # 获取均线数据开始
                self.jx_data_status = 1
                for i in self.all_stock_code:
                    url = "http://api.finance.ifeng.com/akdaily/?code=%s&type=last" % self.all_stock_code[i]['ts_code']
                    threads.append(p.spawn(self.jx_data_func, url, self.all_stock_code[i]['code']))

                    if self.is_limit:
                        self.count += 1
                        if self.count >= self.limit_num:
                            break

                self._jx_data_count = len(threads)
                gevent.joinall(threads)

                # 写入缓存
                with open(jx_cache_file, 'a+') as f:
                    f.write(json.dumps(self.stock_jx_data))

        except Exception as e:
            print("[-] 获取股票均线数据失败!! errcode:100203, errmsg:%s" % e)
            sys.exit()

    def work(self):
        # > ----------------------------- 1. 监听进度线程 ----------------------------- 
        threading.Thread(target=self.status_monitor, args=()).start()

        t1 = threading.Thread(target=self.monitor_money_flow, args=())
        t1.start()

        # > ----------------------------- > 获取股票代码列表&前缀 -----------------------------
        # > ----------------------------- > 获取所有股票代码均线数据 --------------------------
        self.get_all_code()

        # > ----------------------------- > 获取均线数据方法 --------------------------
        self.get_jx_data()
        # 获取均线数据结束
        self.jx_data_status = 2

        # > ----------------------------- > 获取当日收盘数据方法 --------------------------
        while True:
            """
            if int(time.strftime('%H' , time.localtime())) >= 11 and int(time.strftime('%H' , time.localtime())) < 13:
                if int(time.strftime('%M' , time.localtime())) >= 30:
                    print("[-] 午市休息中..")
                    time.sleep(5)
                    continue
            """

            # > ----------------------------- 5. 获取昨日收盘数据方法 --------------------------
            self.get_yestody_stock()
            # 获取昨日数据结束
            self.ys_data_status = 2

            self.jx_data_count = 0
            self.ys_data_count = 0
            self.now_data_count = 0

            self.get_now_stock()
            # 获取当日数据结束
            self.now_data_status = 2

            # > ----------------------------- 7. 套用规则 --------------------------
            self.rule_filter()

            if int(time.strftime('%H' , time.localtime())) >= 15:
                print("[-] 交易已结束, 退出..")
                self.close_signal = True
                sys.exit()

if __name__ == "__main__":
    if not os.path.exists("./config/settings.conf"):
        print("[-] settings.conf is not found, pls check it!")
        sys.exit()
    else:
        # 配置文件加载
        config = ConfigParser.ConfigParser()
        config.read(os.path.join('./config/', 'settings.conf'))

        # 欧赛信令(用于及时发送微信通知)
        token = config.get('base', 'token')

        # 自选股列表(如配置将监控你的自选股资金交易情况)
        is_zxg_monitor = eval(config.get('base', 'is_zxg_monitor'))  # 是否监控自选股列表
        zxg_list = eval(config.get('base', 'zxg_list'))

        # 限制单次获取数量
        is_limit = eval(config.get('base', 'is_limit'))  # 是否限制单次获取数量
        limit_num = int(config.get('base', 'limit_num')) # 限制数量

        # 开始工作
        sn = StockNet(token, is_limit, limit_num)

        # 将当前自选加入监控列表
        if is_zxg_monitor:
            for code in zxg_list:
                sn.rule_matched_list['rule1'].append(code)

        # work
        sn.work()




