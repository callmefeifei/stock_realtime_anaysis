#!coding:utf-8

# 依赖库
import os
import re
import sys
import time
import datetime
import json
import urllib
import requests
import threading
import ConfigParser
from optparse import OptionParser

# 协程
import gevent
from gevent import monkey; monkey.patch_all()
from gevent.pool import Pool

# 请求重试
from requests.adapters import HTTPAdapter

reload(sys)
sys.setdefaultencoding("utf-8")

"""
@ API记录

- 上市以来所有行情指标(实时).
http://push2his.eastmoney.com/api/qt/stock/kline/get?cb=&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&ut=7eea3edcaed734bea9cbfc24409ed989&klt=101&fqt=1&secid=1.600060&beg=0&end=20500000

- 当天全天实时指标(分钟级)
http://push2his.eastmoney.com/api/qt/stock/kline/get?cb=&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&ut=7eea3edcaed734bea9cbfc24409ed989&klt=1&fqt=2&secid=1.600060&beg=0&end=20500000

"""

"""
:TODO
1. 增加异动股票涨停监测
"""

"""
- 短波段:
kdj上升趋势
股票上升趋势
60日区间涨幅不超过20%
资金动向 非减仓 
k值大于d值
k值超过70就有风险了
k值大于d值的天数
上升趋势且k值低的
前一天大涨的，不买，观望

- 操作方式
在股票上升初期，且在k值大于d值初期买入股票，当k值首次跌破d值时卖出.
code, self.stock_jx_data[code][-1]['changepercent'], [ float(s['kdj'][0])>float(s['kdj'][1])  for s in self.stock_jx_data[code]], self.stock_jx_data[code][-1]['kdj'] 
"""

class StockNet():
    def __init__(self, token=None, is_limit=False, limit_num=100, is_notify=False, is_zxg_monitor=False, zxg_list=[], cookie="", appkey=""):
        # 重试请求方法
        self.headers = {
            "Referer":"http://data.eastmoney.com/",
            "User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 11_1_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.141 Safari/537.36"
        }
        self.s = requests.Session()
        self.s.mount('http://', HTTPAdapter(max_retries=2))
        self.s.mount('https://', HTTPAdapter(max_retries=2))
        self.s.headers.update(self.headers)

        # 东方财富请求方法
        self.ds = requests.Session()
        self.ds.mount('http://', HTTPAdapter(max_retries=2))
        self.ds.mount('https://', HTTPAdapter(max_retries=2))
        self.ds_headers = {
            "Referer":"http://data.eastmoney.com/",
            "User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 11_1_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.141 Safari/537.36",
            "Cookie":cookie
        }
        self.ds.headers.update(self.ds_headers)
        self.appkey = appkey

        # 自选股
        self.is_zxg_monitor = is_zxg_monitor
        self.zxg_list = zxg_list
        self.first_zxg_add = True

        # 告警token
        self.wx_token = token

        # 是否发送微信提醒
        self.is_notify = is_notify

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
        self.now_stock_list = []
        self.now_format_stock_dict = {}

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

        # 大数据信息获取状态
        self._anaylse_data_count = 0
        self.anaylse_data_status = 0
        self.anaylse_data_count = 0
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
        self.rule_matched_list['rule6'] = []
        self.rule_matched_list['rule7'] = []
        self.rule_matched_list['rule8'] = []
        self.rule_matched_list['rule9'] = []
        self.rule_matched_list['rule10'] = []
        self.rule_matched_list['kdj'] = []
        self.rule_matched_list['super_kdj'] = []

        # 告警去重
        self.alarm_db = {}

        # 交易结束信号
        self.close_signal = False

        # 异动统计
        self.yd_num_dict = {}

        # 股市分析字典
        self.stock_anaylse_dict = {}

        # 股票池
        self.stock_pool = {}

    def notify(self, s_title, s_content, is_notify, stock_url=None):
        try:
            if is_notify:
                s_title = s_title
                api_url = 'https://api.ossec.cn/v1/send?token=%s' % self.wx_token
                if stock_url:
                    if stock_url.startswith("300") or stock_url.startswith("00"):
                        secid = 0
                    elif stock_url.startswith("68"):
                        pass
                    else:
                        secid = 1

                    stock_url = "https://wzq.tenpay.com/mp/v2/index.html?stat_data=orv53p00gf001#/trade/stock_detail.shtml?scode=%s&type=%s&holder=&frombroker=&remindtype=choose" %  (stock_url, secid)
                    stock_url = urllib.quote(stock_url)
                    api_url += '&topic=%s&message=%s&url=%s' % (s_title, s_content, stock_url)
                else:
                    api_url += '&topic=%s&message=%s' % (s_title, s_content)
                response  = self.s.get(api_url, timeout=60)
        except Exception as e:
            print(e)
            pass

    # 结果保存
    def write_result(self, rule, content):
        filename = time.strftime('%Y%m%d' , time.localtime())+"_"+rule+".txt"

        if not os.path.exists("./result/"):
            os.makedirs("./result/")

        if not os.path.exists("./result/%s" % time.strftime('%Y-%m-%d' , time.localtime())):
            os.makedirs("./result/%s" % time.strftime('%Y-%m-%d' , time.localtime()))

        # 文件路径
        filepath = "./result/%s/%s" % (time.strftime('%Y-%m-%d' , time.localtime()), filename)

        with open(filepath, "a+") as f:
            f.write(content+"\n")

    # 获取数据进度监控
    def status_monitor(self):
        while True:

            # 获取分析数据状态监控
            if self.anaylse_data_status == 0:
                anaylse_data_status = '待开始'
            elif self.anaylse_data_status == 1:
                anaylse_data_status = '获取中'
            elif self.anaylse_data_status == 2:
                anaylse_data_status = '获取完毕'


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

            ntime = "\033[1;32m%s\033[0m" % str(time.strftime('%H:%M:%S' , time.localtime()))
            if self.anaylse_data_status != 0 and self.anaylse_data_status != 3:
                fh = "\033[1;31m*\033[0m"
                n_count = "\033[1;37m%s\033[0m" % str(len(self.all_stock_code.keys())-self.anaylse_data_count)
                if anaylse_data_status == '获取完毕':
                    n_count = 0
                print("[%s][%s] 分析数据获取任务状态 %s , 剩余数量:%s" % (fh, ntime, anaylse_data_status, n_count))
                if self.anaylse_data_status == 2:
                    self.anaylse_data_status = 3

            if self.jx_data_status != 0 and self.jx_data_status != 3:
                fh = "\033[1;31m*\033[0m"
                n_count = "\033[1;37m%s\033[0m" % str(len(self.all_stock_code.keys())-self.jx_data_count)
                if jx_data_status == '获取完毕':
                    n_count = 0
                print("[%s][%s] 均线数据获取任务状态 %s , 剩余数量:%s" % (fh, ntime, jx_data_status, n_count))
                if self.jx_data_status == 2:
                    self.jx_data_status = 3

            if self.ys_data_status != 0 and self.ys_data_status != 3:
                fh = "\033[1;31m*\033[0m"
                n_count = "\033[1;37m%s\033[0m" % str(len(self.stock_jx_data.keys())-self.ys_data_count)
                if ys_data_status == '获取完毕':
                    n_count = 0
                print("[%s][%s] 昨日交易数据获取任务状态 %s , 剩余数量:%s" % (fh, ntime, ys_data_status, n_count))
                if self.ys_data_status == 2:
                    self.ys_data_status = 3

            if self.now_data_status != 0 and self.now_data_status != 0:
                fh = "\033[1;31m*\033[0m"
                n_count = "\033[1;37m%s\033[0m" % str(len(self.stock_jx_data.keys())-self.now_data_count)
                if now_data_status == '获取完毕':
                    n_count = 0
                print("[%s][%s] 当日交易数据获取任务状态 %s , 剩余数量:%s" % (fh, ntime, now_data_status, n_count))
                if self.now_data_status == 2:
                    self.now_data_status = 3

            # 如果都结束, 则退出
            if self.jx_data_status == 3 and self.ys_data_status == 3 and self.now_data_status == 3:
                break

            if self.close_signal:
                break

            time.sleep(5)

    def stock_anaylse(self, code):
        try:
            # T+1智能评分API
            url = "http://quote.eastmoney.com/zixuan/api/znzg?code=%s" % code
            result = self.ds.get(url, timeout=15).json()

            # 主力成本计算API
            zdf_url = "http://dcfm.eastmoney.com/em_mutisvcexpandinterface/api/js/get?type=QGQP_LB&CMD=%s&token=70f12f2f4f091e459a279469fe49eca5&callback=" % code
            con = self.s.get(zdf_url, timeout=15).json()

            name = con[0]['Name']                       # 股票名称
            #yestoday_zdf = con[0]['ChangePercent']      
            kpzt = con[0]['JGCYDType']                  # 控盘状态
            zlcb = con[0]['ZLCB']                       # 主力成本
            rankup = con[0]['RankingUp']                # 近期排行上升还是下降?
            zljlr = float(con[0]['ZLJLR'])/10000        # 主力净流入
            zl_ma20 = con[0]['ZLCB20R']                 # 主力成本ma20
            zl_ma60 = con[0]['ZLCB60R']                 # 主力成本ma60
            yestoday_jlr = '%.2f' % zljlr

            # 昨日涨跌幅
            y_close = result['result']['ApiResults']['zj']['Trend'][1][-1]['Close']
            y_open = result['result']['ApiResults']['zj']['Trend'][1][-2]['Close']
            yestoday_zdf = "%.2f" % ((float(y_close)-float(y_open))/float(y_open)*100)

            # 更新时间
            last_update = result['result']['UpdateTime']

            # DDX
            ddx5 = result['result']['ApiResults']['zj']['Capital'][0]['DDX5']

            # 主力动向
            if '增仓' in result['result']['ApiResults']['zj']['Capital'][0]['zjdx1']:
                zjdx1 = 1 # 增仓
            elif '减仓' in result['result']['ApiResults']['zj']['Capital'][0]['zjdx1']:
                zjdx1 = 0 # 减仓
            else:
                zjdx1 = 2 # 中立

            # 资金动向
            if '流入' in result['result']['ApiResults']['zj']['Capital'][0]['zjdx1']:
                hydx1 = 1 # 流入
            elif '流出' in result['result']['ApiResults']['zj']['Capital'][0]['zjdx1']:
                hydx1 = 0 # 流出
            else:
                hydx1 = 2 # 中立

            # 参与意愿
            cyyy = result['result']['ApiResults']['zj']['Market'][0]['scrd2']
            cyyy_zdf = re.findall('-?\d+\.?\d*e?[-+]?\d*', cyyy)
            if cyyy_zdf:
                cyyy_zdf = cyyy_zdf[0]
                if '上升' not in cyyy:
                    cyyy_zdf = float(cyyy_zdf) / -1
                else:
                    cyyy_zdf = float(cyyy_zdf)
            else:
                cyyy_zdf = 0

            # 平均盈亏
            pjyk = result['result']['ApiResults']['zj']['Market'][0]['scrd3']
            pjyk_zdf = re.findall('-?\d+\.?\d*e?[-+]?\d*', pjyk)
            if pjyk_zdf:
                pjyk_zdf = pjyk_zdf[0]
                if '浮盈' not in pjyk:
                    pjyk_zdf = float(pjyk_zdf) / -1
                else:
                    pjyk_zdf = float(pjyk_zdf)
            else:
                pjyk_zdf = 0

            # 60日盈亏
            yk60r = result['result']['ApiResults']['zj']['Trend'][0][0]['qsyp2']
            yk60r_zdf = re.findall('-?\d+\.?\d*e?[-+]?\d*', yk60r)
            if yk60r_zdf:
                yk60r_zdf = float(yk60r_zdf[0])
            else:
                yk60r_zdf = 0.0

            # 近五净流入
            try:
                capital = result['result']['ApiResults']['zj']['Capital'][0]
                jlr_5days = '%.2f' % (float(capital['Day5MajorNetFlow']) / 10000)
            except:
                jlr_5days = 0

            # 支撑位：26.12
            zcw = result['result']['ApiResults']['zj']['Trend'][0][0]['SupportPosition']
            # 压力位：30.48
            ylw = result['result']['ApiResults']['zj']['Trend'][0][0]['PressurePosition']
            # 综合评分：78
            zhpf = result['result']['ApiResults']['zj']['Overall'][0]['TotalScore']
            # 整体胜率：95.62
            ztsl = result['result']['ApiResults']['zj']['Overall'][0]['LeadPre']
            # 次日胜率：47.39
            crsl = result['result']['ApiResults']['zj']['Overall'][0]['RisePro']
            # 关注指数：90.4
            gzzs = result['result']['ApiResults']['zj']['Market'][0]['FocusScore']
            # 市场成本：28.91
            sccb = result['result']['ApiResults']['zj']['Market'][0]['AvgBuyPrice']
            # 市场排名：121
            scpm = result['result']['ApiResults']['zj']['Market'][0]['Ranking']
            # 今日表现：-0.79
            jrbx = result['result']['ApiResults']['zj']['Overall'][0]['TotalScoreCHG']
            # 成交活跃价格：29.37
            try:
                hyjg = result['result']['ApiResults']['zj']['Capital'][0]['ActivePrice']
            except:
                hyjg = 0
            # 行业排名:19
            hypm = result['result']['ApiResults']['zj']['Value'][0][0]['ValueRanking']
            # 总结
            summary = result['result']['ApiResults']['zj']['Overall'][0]['Comment']
            # 价值评估
            value_summary = result['result']['ApiResults']['zj']['Value'][0][0]['Comment']

            if code not in self.stock_anaylse_dict.keys():
                self.stock_anaylse_dict[code] = {}
                self.stock_anaylse_dict[code] = {
                    "last_update":last_update,
                    "name":name,
                    "yestoday_zdf":yestoday_zdf,
                    "yestoday_jlr":yestoday_jlr,
                    "ddx5":ddx5,
                    "kpzt":kpzt,
                    "zlcb":zlcb,
                    "zjdx1":zjdx1,
                    'hydx1':hydx1,
                    "zl_ma20":zl_ma20,
                    "zl_ma60":zl_ma60,
                    "zljlr":zljlr,
                    "jlr_5days":jlr_5days,
                    "zrjlr":yestoday_jlr,
                    "rankup":rankup,
                    "cyyy_zdf":cyyy_zdf,
                    "pjyk_zdf":pjyk_zdf,
                    "yk60r_zdf":yk60r_zdf,
                    "SupportPosition":zcw,
                    "PressurePosition":ylw,
                    "TotalScore":zhpf,
                    "LeadPre":ztsl,
                    "RisePro":crsl,
                    "FocusScore":gzzs,
                    "AvgBuyPrice":sccb,
                    "Ranking":scpm,
                    "TotalScoreCHG":jrbx,
                    "ActivePrice":hyjg,
                    "ValueRanking":hypm,
                    "summary":summary,
                    "value_summary":value_summary
                }
            else:
                self.stock_anaylse_dict[code] = {
                    "last_update":last_update,
                    "name":name,
                    "yestoday_zdf":yestoday_zdf,
                    "yestoday_jlr":yestoday_jlr,
                    "ddx5":ddx5,
                    "kpzt":kpzt,
                    "zlcb":zlcb,
                    "zjdx1":zjdx1,
                    "hydx1":hydx1,
                    "zl_ma20":zl_ma20,
                    "zl_ma60":zl_ma60,
                    "zljlr":yestoday_jlr,
                    "jlr_5days":jlr_5days,
                    "zrjlr":zrjlr,
                    "rankup":rankup,
                    "cyyy_zdf":cyyy_zdf,
                    "pjyk_zdf":pjyk_zdf,
                    "yk60r_zdf":yk60r_zdf,
                    "SupportPosition":zcw,
                    "PressurePosition":ylw,
                    "TotalScore":zhpf,
                    "LeadPre":ztsl,
                    "RisePro":crsl,
                    "FocusScore":gzzs,
                    "AvgBuyPrice":sccb,
                    "Ranking":scpm,
                    "TotalScoreCHG":jrbx,
                    "ActivePrice":hyjg,
                    "ValueRanking":hypm,
                    "summary":summary,
                    "value_summary":value_summary
                }
        except Exception as e:
            msg = "[-][stock_anaylse] Error : %s %s" % (code, e)
            if 'result' in str(e):
                pass
            #print code, e
            #import pdb;pdb.set_trace()

        self.anaylse_data_count += 1

    def get_anaylse_data(self):
        try:
            # 先判断是否有当日本地缓存, 如果有直接加载
            anaylse_cache_file="./cache/%s_anaylse.json" % str(time.strftime('%Y%m%d' , time.localtime()))
            jx_cache_file="./cache/%s.json" % str(time.strftime('%Y%m%d' , time.localtime()))

            if not os.path.exists("./cache"):
                os.makedirs("./cache/")

            if os.path.exists(anaylse_cache_file):
                with open(anaylse_cache_file, 'r') as f:
                    cache = f.read()

                with open(jx_cache_file, 'r') as f:
                    jx_cache = f.read()

                ntime = "\033[1;32m%s\033[0m" % str(time.strftime('%H:%M:%S' , time.localtime()))
                fh = "\033[1;37m+\033[0m"
                print("[%s][%s] 发现当天数据分析缓存文件, 加载中请稍后..." % (fh, ntime))
                self.stock_anaylse_dict = json.loads(cache)
                self.stock_jx_data = json.loads(jx_cache)

            else:
                # 如果没有, 则重新获取
                p = Pool(200)
                threads = []
                # 获取分析数据
                self.anaylse_data_status = 1
                for i in self.all_stock_code:
                    threads.append(p.spawn(self.stock_anaylse, self.all_stock_code[i]['code']))

                self._anaylse_data_count = len(threads)
                gevent.joinall(threads)

                # 写入缓存
                with open(anaylse_cache_file, 'a+') as f:
                    f.write(json.dumps(self.stock_anaylse_dict))
        except:
            pass

    # 获取现价以及涨跌幅
    def fetch_now_changepercent(self, secid, code):
        try:
            url = "http://push2.eastmoney.com/api/qt/stock/get?cb=&fltt=2&invt=2&secid=%s.%s&fields=f43,f170" % (secid, code)
            con = self.s.get(url, timeout=3).json()
            trade = con['data']['f43']
            now_changepercent = con['data']['f170']
        except:
            trade = 0
            now_changepercent = 0

        return trade, now_changepercent

    # 资金流入方法计算
    def money_flow_calc(self, in_money_1, in_money_2):
        money_flow_bs = 1.0

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

        return money_flow_bs

    # 计数器
    def yd_count(self, code):
        # 180s(3分钟)内出现3次+大幅流入, 则告警

        ntime = int(time.time())
        if code in self.yd_num_dict.keys():
            print code, self.yd_num_dict[code]
            # 首先查看上次记录截止目前是否过期(180s)
            if ntime - self.yd_num_dict[code][0] >= 180:
                self.yd_num_dict[code][0] = ntime
                self.yd_num_dict[code][1] = 1
                return False
            else:
                n_count = self.yd_num_dict[code][1]
                n_count += 1
                self.yd_num_dict[code][1] = n_count
                if n_count >= 3:
                    return True
                else:
                    return False

        else:
            self.yd_num_dict[code] = [ntime, 1]
            return False

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
            data = self.s.get(url, timeout=5).json()['data']
            klines = data['klines']
            name = data['name']

            # 现价
            #now_trade, now_changepercent = self.fetch_now_changepercent(secid, code)
            try:
                url = "http://push2.eastmoney.com/api/qt/stock/get?cb=&fltt=2&invt=2&secid=%s.%s&fields=f43,f170" % (secid, code)
                con = self.s.get(url, timeout=5).json()
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

            if code in self.yestoday_stock_dict.keys():
                #print "[*][%s][%s][%s][%s] 现价:%s 涨跌幅:%s 当前资金净流入:%.2f万 近一分钟净流入:%.2f万 与上分钟比资金流入倍数:%.2f | jlr_5days: %.2f | zdf_5days: %.2f" % (time.strftime('%Y-%m-%d %H:%M:%S' , time.localtime()), rule_type, code, name, now_trade, now_changepercent, in_money_2/10000, min1flow, money_flow_bs, self.yestoday_stock_dict[code]['jlr_5days'], self.now_stock_dict[code]['zdf_5d'])
                pass

            # 判断是否程序打开后首次分析该股票?如果是，则不管资金是否大幅流入, 均写下一一条记录.
            if code not in self.stock_pool.keys():
                self.stock_pool[code] = {}
                self.stock_pool[code]['is_frist_exec'] = True
            else:
                self.stock_pool[code]['is_frist_exec'] = False

            # 资金流入倍数>1.5, 则认为异动. 资金流入倍数 -x, 跌
            is_matched = False
            if money_flow_bs <= -1.5:
                note = "出现大幅流出."
                is_matched = True

            elif money_flow_bs >= 1.5:
                note = "出现大幅流入."
                is_matched = True

            elif self.stock_pool[code]['is_frist_exec'] is True:
                note = "首次记录"
                is_matched = True

            if is_matched:
                _jlr = (float(in_money_2) - float(in_money_1))/10000
                content = "发现股票存在异动, 股票代码: [%s][%s][%s][%s][%s万] | 命中规则: %s | 信号: %s | 流入: %.2f万 | 与上分钟比资金倍数: %.2f"  % (code, name, now_trade, now_changepercent, in_money_2/10000, rule_type, note, _jlr, money_flow_bs)
                _content = "[*][%s][%s][%s][现价:%s][涨跌幅:%s][净流入:%.2f万] 发现异动 | 命中规则: %s | 信号: %s | 与上分钟比资金倍数: %.2f | jlr_5days: %.2f | zdf_5days: %.2f"  % (time.strftime('%Y-%m-%d %H:%M:%S' , time.localtime()), code, name, now_trade, now_changepercent, in_money_2/10000, rule_type, note, money_flow_bs, self.yestoday_stock_dict[code]['jlr_5days'], self.now_stock_dict[code]['zdf_5d'])

                # 判断是否已告警过
                if code not in self.alarm_db.keys():
                    if '大幅流出' not in note:
                        if money_flow_bs >= 10 and float(_jlr) > 100:
                            is_continue_in = self.yd_count(code)
                            #self.notify("发现异动股票", content, True, code)
                        else:
                            is_continue_in = self.yd_count(code)
                            #self.notify("发现异动股票", content, self.is_notify, code)

                        # 持续流入
                        if is_continue_in:
                            #self.notify("股票正在持续流入", content, True, code)
                            pass

                    self.alarm_db[code] = {in_money_2:True}
                else:
                    if in_money_2 in self.alarm_db[code].keys():
                        pass
                    else:
                        if '大幅流出' not in note:
                            if money_flow_bs >= 10 and float(_jlr) > 100:
                                is_continue_in = self.yd_count(code)
                                #self.notify("发现异动股票", content, True, code)
                            else:
                                is_continue_in = self.yd_count(code)
                                #self.notify("发现异动股票", content, self.is_notify, code)

                            # 持续流入
                            if is_continue_in:
                                #self.notify("股票正在持续流入", content, True, code)
                                pass

                        self.alarm_db[code] = {in_money_2:True}

                # 记录结果到本地
                self.write_result("money_flow", _content)
                self.stock_pool[code]['is_frist_exec'] = False

                # 如果是首次记录, 且时间 > 9点40, 且rule=6，特别告警.
                if note == '首次记录' and int(time.strftime('%H' , time.localtime())) >= 9:
                    if int(time.strftime('%H' , time.localtime())) == 9 and int(time.strftime('%M' , time.localtime())) >= 50:
                        # 告警
                        self.notify("异动首次命中规则, 快关注!!", content, True, code)
                    else:
                        # 告警
                        self.notify("异动首次命中规则, 快关注!!", content, True, code)

        except Exception as e:
            if "float division by zero" in str(e):
                pass
            elif "nodename nor servname provided, or not known" in str(e):
                pass
            else:
                print("[-] 获取获取资金流量方法失败!! errcode:100205, errmsg:%s" % e)

    def monitor_money_flow(self, once=False):
        # 循环监控
        while True:
            try:

                if int(time.strftime('%H' , time.localtime())) >= 11 and int(time.strftime('%H' , time.localtime())) < 13:
                    if int(time.strftime('%H' , time.localtime())) == 11:
                        if int(time.strftime('%M' , time.localtime())) >= 30:
                            print("[-] 午市休息中..")
                            time.sleep(5)
                            continue
                    elif int(time.strftime('%H' , time.localtime())) == 12 and int(time.strftime('%M' , time.localtime())) >= 55:
                        pass
                    else:
                        print("[-] 午市休息中..")
                        time.sleep(5)
                        continue

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

                # rule6
                rule6_list = self.rule_matched_list['rule6']

                # rule7
                rule7_list = self.rule_matched_list['rule7']

                # rule8
                rule8_list = self.rule_matched_list['rule8']

                # rule9
                rule9_list = self.rule_matched_list['rule9']

                # rule10
                rule10_list = self.rule_matched_list['rule10']

                # super_kdj
                super_kdj_list = self.rule_matched_list['super_kdj']

                # kdj
                kdj_list = self.rule_matched_list['kdj']

                p = Pool(300)
                threads = []
                # rule1
                for code in rule1_list:
                    try:
                        threads.append(p.spawn(self.fetch_money_flow, code, "rule1"))
                    except:
                        pass

                # rule2
                for code in rule2_list:
                    try:
                        threads.append(p.spawn(self.fetch_money_flow, code, "rule2"))
                    except:
                        pass

                # rule3
                for code in rule2_list:
                    try:
                        threads.append(p.spawn(self.fetch_money_flow, code, "rule3"))
                    except:
                        pass

                # rule4
                for code in rule4_list:
                    try:
                        threads.append(p.spawn(self.fetch_money_flow, code, "rule4"))
                    except:
                        pass

                # rule5
                for code in rule5_list:
                    try:
                        threads.append(p.spawn(self.fetch_money_flow, code, "rule5"))
                    except:
                        pass

                # rule6
                for code in rule6_list:
                    try:
                        threads.append(p.spawn(self.fetch_money_flow, code, "rule6"))
                    except:
                        pass

                # rule7
                for code in rule7_list:
                    try:
                        threads.append(p.spawn(self.fetch_money_flow, code, "rule7"))
                    except:
                        pass

                # rule8
                for code in rule8_list:
                    try:
                        threads.append(p.spawn(self.fetch_money_flow, code, "rule8"))
                    except:
                        pass

                # rule9
                for code in rule9_list:
                    try:
                        threads.append(p.spawn(self.fetch_money_flow, code, "rule9"))
                    except:
                        pass

                # rule10
                for code in rule10_list:
                    try:
                        threads.append(p.spawn(self.fetch_money_flow, code, "rule10"))
                    except:
                        pass

                # super_kdj
                for code in super_kdj_list:
                    try:
                        threads.append(p.spawn(self.fetch_money_flow, code, "super_kdj"))
                    except:
                        pass

                # kdj
                for code in kdj_list:
                    try:
                        threads.append(p.spawn(self.fetch_money_flow, code, "kdj"))
                    except:
                        pass

                gevent.joinall(threads)

                # 如果是执行一次，则退出
                if once:
                    break

                # 15秒获取一次实时资金信息
                time.sleep(20)

                if self.close_signal:
                    break
            except Exception as e:
                print("[-] 监控资金流入数据失败!! errcode:100300, errmsg:%s" % e)
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
                res = self.s.get("https://suggest3.sinajs.cn/suggest/type=11,12,13,14,15,72&key=%s" % code , timeout=3).text.split('"')[1]
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
            all_trade_time = [i[0] for i in self.s.get("http://api.finance.ifeng.com/akdaily/?code=sz002307&type=last", timeout=3).json()['record']]
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
            stock_code_list = [ i['f12'] for i in self.s.get("http://21.push2.eastmoney.com/api/qt/clist/get?cb=&pn=1&pz=10000&po=1&np=1&ut=&fltt=2&invt=2&fid=f3&fs=m:0+t:6,m:0+t:13,m:0+t:80,m:1+t:2,m:1+t:23&fields=f2,f3,f12,f14", timeout=3).json()['data']['diff'] ]
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
        except Exception as e:
            ma5 = 0
            ma10 = 0
            ma30 = 0
            trade = 0
        
        return ma5, ma10, ma30, trade

    # 获取昨天收盘数据方法
    def yestody_data_func(self, code, url, ts_code):
        # 获取当前均线价格
        try:
            ma_api = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData?symbol=%s&scale=15&datalen=1" % (ts_code)
            con = self.s.get(url=ma_api, timeout=10).json()[0]
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
        #print code, self.last_date, y_ma5, y_ma10, y_ma30, y_trade

        # 获取昨日资金详情
        try:
            con = self.s.get(url, timeout=10).json()

            if len(con) <= 1:
                print code, con

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
            #print(0,e)
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
            name = self.stock_anaylse_dict[code]['name']                    # 股票名称
            score = self.stock_anaylse_dict[code]['TotalScore']             # 得分
            rank = self.stock_anaylse_dict[code]['Ranking']                 # 排名
            focus = self.stock_anaylse_dict[code]['FocusScore']             # 关注度
            kpzt = self.stock_anaylse_dict[code]['kpzt']                    # 控盘状态
            zlcb = self.stock_anaylse_dict[code]['zlcb']                    # 主力成本
            zjdx1 = self.stock_anaylse_dict[code]['zjdx1']                  # 主力资金动向
            hydx1 = self.stock_anaylse_dict[code]['hydx1']                  # 行业资金动向
            rankup = self.stock_anaylse_dict[code]['rankup']                # 近期排行上升还是下降?
            summary = self.stock_anaylse_dict[code]['summary']              # 总结
            value_summary = self.stock_anaylse_dict[code]['value_summary']  # 价值总结
            hypp = self.stock_anaylse_dict[code]['ValueRanking']            # 行业排名
            cjjj = self.stock_anaylse_dict[code]['ActivePrice']             # 成交活跃价格
            sccb = self.stock_anaylse_dict[code]['AvgBuyPrice']             # 市场成本
            crsl = self.stock_anaylse_dict[code]['RisePro']                 # 次日胜率
            drbx = self.stock_anaylse_dict[code]['LeadPre']                 # 当日表现
            ylw = self.stock_anaylse_dict[code]['PressurePosition']         # 压力位
            zcw = self.stock_anaylse_dict[code]['SupportPosition']          # 支撑位
            cyyy = self.stock_anaylse_dict[code]['cyyy_zdf']                # 参与意愿
            pjyk = self.stock_anaylse_dict[code]['pjyk_zdf']                # 平均盈亏
            yk60r_zdf = self.stock_anaylse_dict[code]['yk60r_zdf']          # 平均盈亏
            yestoday_jlr = self.stock_anaylse_dict[code]['yestoday_jlr']    # 昨日净流入
            yestoday_zdf = self.stock_anaylse_dict[code]['yestoday_zdf']    # 昨日涨跌幅
            jlr_5days = self.stock_anaylse_dict[code]['jlr_5days']          # 近五日净流入

            if code not in self.now_stock_dict.keys():
                self.now_stock_dict[code] = {}
                self.now_stock_dict[code]['code'] = code
                self.now_stock_dict[code]['name'] = name
                self.now_stock_dict[code]['score'] = score
                self.now_stock_dict[code]['rank'] = rank
                self.now_stock_dict[code]['focus'] = focus
                self.now_stock_dict[code]['kpzt'] = kpzt
                self.now_stock_dict[code]['zlcb'] = zlcb
                self.now_stock_dict[code]['zjdx1'] = zjdx1
                self.now_stock_dict[code]['hydx1'] = hydx1
                self.now_stock_dict[code]['rankup'] = rankup
                self.now_stock_dict[code]['summary'] = summary
                self.now_stock_dict[code]['value_summary'] = value_summary
                self.now_stock_dict[code]['hypp'] = hypp
                self.now_stock_dict[code]['cjjj'] = cjjj
                self.now_stock_dict[code]['sccb'] = sccb
                self.now_stock_dict[code]['crsl'] = crsl
                self.now_stock_dict[code]['drbx'] = drbx
                self.now_stock_dict[code]['ylw'] = ylw
                self.now_stock_dict[code]['zcw'] = zcw
                self.now_stock_dict[code]['cyyy'] = cyyy
                self.now_stock_dict[code]['pjyk'] = pjyk
                self.now_stock_dict[code]['yk60r_zdf'] = yk60r_zdf
                self.now_stock_dict[code]['zdf'] = 0
                self.now_stock_dict[code]['jlr'] = 0
                self.now_stock_dict[code]['yestoday_jlr'] = yestoday_jlr
                self.now_stock_dict[code]['yestoday_zdf'] = yestoday_zdf
                self.now_stock_dict[code]['jlr_5days'] = jlr_5days
            else:
                self.now_stock_dict[code]['code'] = code
                self.now_stock_dict[code]['name'] = name
                self.now_stock_dict[code]['score'] = score
                self.now_stock_dict[code]['rank'] = rank
                self.now_stock_dict[code]['focus'] = focus
                self.now_stock_dict[code]['kpzt'] = kpzt
                self.now_stock_dict[code]['zlcb'] = zlcb
                self.now_stock_dict[code]['zjdx1'] = zjdx1
                self.now_stock_dict[code]['hydx1'] = hydx1
                self.now_stock_dict[code]['rankup'] = rankup
                self.now_stock_dict[code]['summary'] = summary
                self.now_stock_dict[code]['value_summary'] = value_summary
                self.now_stock_dict[code]['hypp'] = hypp
                self.now_stock_dict[code]['cjjj'] = cjjj
                self.now_stock_dict[code]['sccb'] = sccb
                self.now_stock_dict[code]['crsl'] = crsl
                self.now_stock_dict[code]['drbx'] = drbx
                self.now_stock_dict[code]['ylw'] = ylw
                self.now_stock_dict[code]['zcw'] = zcw
                self.now_stock_dict[code]['cyyy'] = cyyy
                self.now_stock_dict[code]['pjyk'] = pjyk
                self.now_stock_dict[code]['yk60r_zdf'] = yk60r_zdf
                self.now_stock_dict[code]['zdf'] = 0
                self.now_stock_dict[code]['jlr'] = 0
                self.now_stock_dict[code]['yestoday_jlr'] = yestoday_jlr
                self.now_stock_dict[code]['yestoday_zdf'] = yestoday_zdf
                self.now_stock_dict[code]['jlr_5days'] = jlr_5days
        except Exception as e:
            #print(0,e)
            pass

        self.now_data_count += 1

    # 获取当日收盘数据
    def get_now_stock(self):
        try:
            all_url = self.get_all_url(flag=2)

            p = Pool(150)
            threads = []

            # 获取当日实时数据(市场排行、控盘状态)
            self.now_data_status = 1
            for u in all_url:
                code = u[0]
                url = u[1]
                ts_code = u[2]
                threads.append(p.spawn(self.now_data_func, code, url, ts_code))

            gevent.joinall(threads)

            # 获取当日股票数据(所属行业、主力实时排名)
            url = "http://push2.eastmoney.com/api/qt/clist/get?cb=&fid=f184&po=1&pz=5000&pn=1&np=1&fltt=2&invt=2&fields=f2,f3,f12,f13,f14,f62,f184,f225,f165,f263,f109,f175,f264,f160,f100,f124,f265&ut=b2884a393a59ad64002292a3e90d46a5&fs=m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,m:1+t:23+f:!2,m:0+t:7+f:!2,m:1+t:3+f:!2"
            data = self.s.get(url, timeout=5).json()['data']['diff']
            for stock in data:
                try:
                    code = stock['f12']                 # 股票代码
                    industry = stock['f100']            # 所属行业
                    zlrank_today = stock['f225']        # 今日排名
                    zlrank_5d = stock['f263']           # 五日主力排名
                    zdf_5d = stock['f109']              # 近五日涨跌幅
                    zlrannk_10d = stock['f264']         # 十日主力排名
                    zdf_10d = stock['f160']             # 近十日涨跌幅

                    if code in self.now_stock_dict.keys():
                        self.now_stock_dict[code]['industry'] = industry
                        self.now_stock_dict[code]['zlrank_today'] = zlrank_today
                        self.now_stock_dict[code]['zlrank_5d'] = zlrank_5d
                        self.now_stock_dict[code]['zdf_5d'] = zdf_5d
                        self.now_stock_dict[code]['zlrannk_10d'] = zlrannk_10d
                        self.now_stock_dict[code]['zdf_10d'] = zdf_10d
                except Exception as e:
                    #print(1,e)
                    continue

            # 获取当日股票数据(最新价、涨跌幅、资金资金实时流入情况)
            url = "http://push2.eastmoney.com/api/qt/clist/get?cb=&fid=f62&po=1&pz=5000&pn=1&np=1&fltt=2&invt=2&ut=b2884a393a59ad64002292a3e90d46a5&fs=m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,m:1+t:23+f:!2,m:0+t:7+f:!2,m:1+t:3+f:!2&fields=f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f204,f205,f124"
            data = self.s.get(url, timeout=5).json()['data']['diff']
            for stock in data:
                try:
                    code = stock['f12']                 # 股票代码
                    name = stock['f14']                 # 股票名称
                    trade = stock['f2']                 # 最新价
                    zdf = stock['f3']                   # 涨跌幅
                    jlr = stock['f62']/10000                  # 主力净流入
                    cddjlr = stock['f66']/10000               # 超大单净流入
                    ddjlr = stock['f72']/10000                # 大单净流入
                    zdjlr = stock['f78']/10000                # 中单净流入
                    xdjlr = stock['f84']/10000                # 小单净流入

                    if code in self.now_stock_dict.keys():
                        self.now_stock_dict[code]['zdf'] = zdf
                        self.now_stock_dict[code]['jlr'] = jlr
                        self.now_stock_dict[code]['trade'] = trade
                        self.now_stock_dict[code]['cddjlr'] = cddjlr
                        self.now_stock_dict[code]['ddjlr'] = ddjlr
                        self.now_stock_dict[code]['zdjlr'] = zdjlr
                        self.now_stock_dict[code]['xdjlr'] = xdjlr
                except Exception as e:
                    #print(2,e)
                    continue

            # 获取当日股票市值
            url = "http://18.push2.eastmoney.com/api/qt/clist/get?cb=&pn=1&pz=10000&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2&invt=2&fid=f3&fs=m:0+t:6,m:0+t:13,m:0+t:80,m:1+t:2,m:1+t:23&fields=f12,f14,f20"
            data = self.s.get(url, timeout=5).json()['data']['diff']
            for stock in data:
                try:
                    code = stock['f12']                         # 股票代码
                    name = stock['f14']                         # 股票名称
                    volume = float(stock['f20'])/100000000       # 市值

                    if code in self.now_stock_dict.keys():
                        self.now_stock_dict[code]['volume'] = volume
                except:
                    continue

            # 生成今日股票数据列表
            self.now_stock_list = []
            for i in self.now_stock_dict:
                self.now_stock_list.append(self.now_stock_dict[i])

        except Exception as e:
            print("[-] 获取今日股票收盘数据失败!! errcode:100208, errmsg:%s" % e)
            return

    # 均线数据获取赋值
    def jx_data_func(self, url, ts_code, code):
        try:
            jx_data = self.s.get(url, timeout=5).json()

            # 新股(小于60日)
            if jx_data['re'] is False:
                url = "http://api.finance.ifeng.com/akdaily/?code=%s&type=last" % ts_code
                jx_data = self.s.get(url, timeout=5).json()['record'][-5:]

        except Exception as e:
            jx_data = []

        if jx_data:
            if isinstance(jx_data, dict):
                # k线数据(仅取后5天，否则将获取不到数据)
                stock_jx_data = jx_data['result']['ApiResults']['zj']['Trend'][1][-5:]
                # 技术指标(MACD、KDJ等)
                stock_js_data = {}
                for i in jx_data['result']['ApiResults']['zj']['Trend'][2]:
                    TDate = "%s-%02d-%02d" % (i['TDate'].split("/")[0], float(i['TDate'].split("/")[1]), float(i['TDate'].split("/")[2].split(' ')[0]))
                    stock_js_data[TDate] = i
            else:
                stock_jx_data = jx_data

            for stock in stock_jx_data:
                try:
                    if isinstance(stock, dict):
                        stock_dict = {}
                        stock_dict['date'] = "%s-%02d-%02d" % (stock['TDate'].split("/")[0], float(stock['TDate'].split("/")[1]), float(stock['TDate'].split("/")[2].split(' ')[0]))
                        stock_dict['open'] = stock['Open']
                        stock_dict['high'] = stock['High']
                        stock_dict['close'] = stock['Close']
                        stock_dict['low'] = stock['Low']
                        stock_dict['changepercent'] = (float(stock['Close'])-float(stock['Open']))/float(stock['Open'])*100
                        stock_dict['ma5'] = stock['Price5']
                        stock_dict['ma10'] = stock['Price20']
                        stock_dict['ma20'] = stock['Price60']
                        #stock_dict['hsl'] = 0 # 无该数据指标

                        if stock_dict['date'] in stock_js_data.keys():
                            js = stock_js_data[stock_dict['date']]
                            stock_dict['macd'] = [js['MACD'], js['DIFF'], js['DEA']]
                            stock_dict['kdj'] = [js['K'], js['D'], js['J']]
                        else:
                            stock_dict['macd'] = [0, 0, 0]
                            stock_dict['kdj'] = [0, 0, 0]
                    else:
                        stock_dict = {}
                        stock_dict['date'] = stock[0]
                        stock_dict['open'] = stock[1]
                        stock_dict['high'] = stock[2]
                        stock_dict['close'] = stock[3]
                        stock_dict['low'] = stock[4]
                        stock_dict['changepercent'] = stock[7]
                        stock_dict['ma5'] = stock[8]
                        stock_dict['ma10'] = stock[9]
                        stock_dict['ma20'] = stock[10]
                        #stock_dict['hsl'] = stock[14] # 无该数据指标
                        stock_dict['macd'] = [0, 0, 0]
                        stock_dict['kdj'] = [0, 0, 0]

                    if code in self.stock_jx_data.keys():
                        self.stock_jx_data[code].append(stock_dict)
                    else:
                        self.stock_jx_data[code] = []
                        self.stock_jx_data[code].append(stock_dict)

                except Exception as e:
                    print(e)
                    continue

        # 只保存最多最后五个交易日的
        if code in self.stock_jx_data.keys():
            if len(self.stock_jx_data[code]) >= 6:
                self.stock_jx_data[code] = self.stock_jx_data[code][-5:]

        self.jx_data_count += 1

    # 过滤掉坏股票
    def filter_bad(self, code):
        try:
            # 股票整体得分不小于60分
            if float(self.stock_anaylse_dict[code]['TotalScore']) < 60:
                return False

            # 市场关注度不小于60分
            if float(self.stock_anaylse_dict[code]['FocusScore']) < 60:
                return False

            # 上涨概率不小于45分
            if float(self.stock_anaylse_dict[code]['RisePro']) <= 45:
                return False

            # 市场平均表现不小于60分
            if float(self.stock_anaylse_dict[code]['LeadPre']) < 60:
                return False

            # 参与意愿不能小于0
            if float(self.now_stock_dict[code]['cyyy']) < 0:
                return False

            # 主力不可呈流出状态
            if '流出' in self.now_stock_dict[code]['summary']:
                return False

            # 股票质地不能太差
            if '质地很差' in self.now_stock_dict[code]['value_summary'] or '质地不佳' in self.now_stock_dict[code]['value_summary']:
                return False

            # 如果当前主力资金净流入>负1000w, 直接false.
            if self.now_stock_dict[code]['jlr'] <= -1000:
                return False

            # 当前净流出的
            if float(self.now_stock_dict[code]['jlr']) < 0:
                return False

            # kdj检测
            # 1. 如果k<d, pass
            # 2. 如果k>75, pass
            if float(self.stock_jx_data[code][-1]['kdj'][0]) >= 75:
                return False

            if float(self.stock_jx_data[code][-1]['kdj'][0]) < float(self.stock_jx_data[code][-1]['kdj'][1]):
                return False

            # 行业资金为流入状态 或 主力资金增仓
            # 10点前 > 300w 则不判断行业资金与主力资金了
            is_pass = False
            if int(time.strftime('%H' , time.localtime())) >= 9 and int(time.strftime('%H' , time.localtime())) <= 10:
                if self.now_stock_dict[code]['jlr'] > 300:
                    is_pass = True

            # 13点后 > 1000w 则不判断行业资金与主力资金了
            if int(time.strftime('%H' , time.localtime())) >= 13 and int(time.strftime('%H' , time.localtime())) <= 15:
                if self.now_stock_dict[code]['jlr'] > 1000:
                    is_pass = True

            # 如果行业资金流出、主力资金流出，且当前净流入不满足以上条件, 则返回false
            if self.now_stock_dict[code]['hydx1'] == 0:
                if self.now_stock_dict[code]['zjdx1'] == 0:
                    if is_pass is False:
                        return False

            return True
        except Exception as e:
            print("bad", e)
            return False

    # 将匹配到的股票加入到规则列表
    # --- 同时进行二次判断 ---
    def add2matched(self, rule, code):
        try:
            # 股票整体得分不小于60分
            if float(self.stock_anaylse_dict[code]['TotalScore']) < 60:
                return False

            # 市场关注度不小于60分
            if float(self.stock_anaylse_dict[code]['FocusScore']) < 60:
                return False

            # 上涨概率不小于45分
            if float(self.stock_anaylse_dict[code]['RisePro']) <= 45:
                return False

            # 市场平均表现不小于60分
            if float(self.stock_anaylse_dict[code]['LeadPre']) < 60:
                return False

            # 参与意愿不能小于0
            if float(self.now_stock_dict[code]['cyyy']) < 0:
                return False

            # 主力不可流出状态
            if '流出' in self.now_stock_dict[code]['summary']:
                return False

            # 股票质地不能太差
            if '质地很差' in self.now_stock_dict[code]['value_summary'] or '质地不佳' in self.now_stock_dict[code]['value_summary']:
                return False

            # 如果当前主力资金净流入>负1000w, 直接false.
            if self.now_stock_dict[code]['jlr'] <= -1000:
                return False

            # kdj检测
            # 1. 如果k<d, pass
            # 2. 如果k>75, pass
            if float(self.stock_jx_data[code][-1]['kdj'][0]) >= 75:
                return False

            if float(self.stock_jx_data[code][-1]['kdj'][0]) < float(self.stock_jx_data[code][-1]['kdj'][1]):
                return False

            # 行业资金为流入状态 或 主力资金增仓
            # 10点前 > 300w 则不判断行业资金与主力资金了
            is_pass = False
            if int(time.strftime('%H' , time.localtime())) >= 9 and int(time.strftime('%H' , time.localtime())) <= 10:
                if self.now_stock_dict[code]['jlr'] > 300:
                    is_pass = True

            # 13点后 > 1000w 则不判断行业资金与主力资金了
            if int(time.strftime('%H' , time.localtime())) >= 13 and int(time.strftime('%H' , time.localtime())) <= 15:
                if self.now_stock_dict[code]['jlr'] > 1000:
                    is_pass = True

            # 如果行业资金流出、主力资金流出，且当前净流入不满足以上条件, 则返回false
            if self.now_stock_dict[code]['hydx1'] == 0:
                if self.now_stock_dict[code]['zjdx1'] == 0:
                    if is_pass is False:
                        return False

            self.rule_matched_list[rule].append(code)
            return True
        except:
            return False

    # 通过规则筛选需要股票
    def rule_filter(self):
        # rule6: 今日活跃股票
        # 1. 今日排名topN的

        try:
            rule6_list = []
            # 首先按当日资金流入排名排序, 获取top50
            for i in sorted(self.now_stock_list, key=lambda x:x['zlrank_today'], reverse=False):
                if len(rule6_list) >= 50:
                    break
                else:
                    # 排除st、排除涨跌幅 > 5 的
                    if 'ST' in i['name'] or i['zdf'] > 5 or i['code'].startswith("68"):
                            continue
                    else:
                        rule6_list.append(i)


            # 最后按实时资金净流入排行, 获取top50
            for i in sorted(self.now_stock_list, key=lambda x:x['jlr'], reverse=True):
                if len(rule6_list) >= 100:
                    break
                else:
                    # 排除st、排除涨跌幅 > 5 的
                    if 'ST' in i['name'] or i['zdf'] > 5 or i['code'].startswith("68") or i in rule6_list:
                            continue
                    else:
                        rule6_list.append(i)

            # 然后分别获取3个(rank、zlrank_today、score)排序top10
            _rule6_list = []
            for i in sorted(rule6_list, key=lambda x:x['zlrank_today'], reverse=False):
                if len(_rule6_list) >= 25:
                    break
                else:
                    if i in _rule6_list:continue
                    _rule6_list.append(i)

            for i in sorted(rule6_list, key=lambda x:x['jlr'], reverse=True):
                if len(_rule6_list) >= 25:
                    break
                else:
                    if i in _rule6_list:continue
                    _rule6_list.append(i)

            for i in sorted(_rule6_list, key=lambda x:x['score'], reverse=True):
                code = i['code']
                stock = code
                name = i['name']
                zdf = self.yestoday_stock_dict[code]['zdf']
                jlr = self.yestoday_stock_dict[code]['jlr']
                kdj_num = [ float(s['kdj'][0])>float(s['kdj'][1])  for s in self.stock_jx_data[code]].count(True)
                #kdj_avg = (float(self.stock_jx_data[code][-1]['kdj'][0])+float(self.stock_jx_data[code][-1]['kdj'][1])+float(self.stock_jx_data[code][-1]['kdj'][2]))/3
                #kdj_avg = "%.2f" % kdj_avg
                kdj_avg = [ "%.2f" % (sum([float(ii) for ii in s['kdj']])/3) for s in self.stock_jx_data[code]]
                kdj_avg = "%s->%s->%s->%s->%s" % (kdj_avg[0], kdj_avg[1], kdj_avg[2], kdj_avg[3], kdj_avg[4])
                k = "%.2f" % float(self.stock_jx_data[code][-1]['kdj'][0])
                d = "%.2f" % float(self.stock_jx_data[code][-1]['kdj'][1])
                fh = "\033[1;37m+\033[0m"
                content = "[%s][%s][rule6][%s][%s][kdj_num:%s][kd:%s,%s][kdj_avg:%s][zlrank:%s][score:%s][rank:%s] 昨日净流入:%s 昨日涨跌幅:%s 今日净流入:%s 今日涨跌幅:%s 近五净流入:%s万 近期涨跌幅(5/10):%s/%s" % (fh, time.strftime('%Y-%m-%d %H:%M:%S' , time.localtime()), code, self.yestoday_stock_dict[stock]['name'], kdj_num, k, d, kdj_avg, self.now_stock_dict[code]['zlrank_today'],self.now_stock_dict[code]['score'],self.now_stock_dict[code]['rank'], jlr, zdf, self.now_stock_dict[stock]['jlr'], self.now_stock_dict[stock]['zdf'], self.yestoday_stock_dict[stock]['jlr_5days'], self.now_stock_dict[stock]['zdf_5d'], self.now_stock_dict[code]['zdf_10d'])
                if code not in self.rule_matched_list['rule6']:
                    if self.filter_bad(code):
                        print content
                        self.add2matched("rule6", code)
                        self.write_result("rule6", content)
                        self.rule_matched_list['rule6'].append(code)

            # 自选股
            if self.is_zxg_monitor:
                for code in self.zxg_list:
                    stock = code
                    name = self.yestoday_stock_dict[code]['name']
                    zdf = self.yestoday_stock_dict[code]['zdf']
                    jlr = self.yestoday_stock_dict[code]['jlr']
                    kdj_num = [ float(s['kdj'][0])>float(s['kdj'][1])  for s in self.stock_jx_data[code]].count(True)
                    #kdj_avg = (float(self.stock_jx_data[code][-1]['kdj'][0])+float(self.stock_jx_data[code][-1]['kdj'][1])+float(self.stock_jx_data[code][-1]['kdj'][2]))/3
                    #kdj_avg = "%.2f" % kdj_avg
                    kdj_avg = [ "%.2f" % (sum([float(ii) for ii in s['kdj']])/3) for s in self.stock_jx_data[code]]
                    kdj_avg = "%s->%s->%s->%s->%s" % (kdj_avg[0], kdj_avg[1], kdj_avg[2], kdj_avg[3], kdj_avg[4])
                    k = "%.2f" % float(self.stock_jx_data[code][-1]['kdj'][0])
                    d = "%.2f" % float(self.stock_jx_data[code][-1]['kdj'][1])
                    fh = "\033[1;37m+\033[0m"
                    content = "[%s][%s][rule1][%s][%s][kdj_num:%s][kd:%s,%s][kdj_avg:%s][zlrank:%s][score:%s][rank:%s] 昨日净流入:%s 昨日涨跌幅:%s 今日净流入:%s 今日涨跌幅:%s 近五净流入:%s万 近期涨跌幅(5/10):%s/%s" % (fh, time.strftime('%Y-%m-%d %H:%M:%S' , time.localtime()), code, self.yestoday_stock_dict[stock]['name'], kdj_num, k, d, kdj_avg, self.now_stock_dict[code]['zlrank_today'],self.now_stock_dict[code]['score'],self.now_stock_dict[code]['rank'], jlr, zdf, self.now_stock_dict[stock]['jlr'], self.now_stock_dict[stock]['zdf'], self.yestoday_stock_dict[stock]['jlr_5days'], self.now_stock_dict[stock]['zdf_5d'], self.now_stock_dict[code]['zdf_10d'])

                    if code not in self.rule_matched_list['rule1']:
                        if self.filter_bad(code):
                            print content
                            self.add2matched("rule1", code)
                            self.write_result("rule1", content)
                            self.rule_matched_list['rule1'].append(code)

        except Exception as e2:
            pass

        # rule5: 近五日净流入大 & 近五日涨跌幅小
        # 1. 近五日资金净流入 > 0 且 资金净流入排名靠前(top100?)
        # 2. 近五日涨跌幅 >= 0 and 近五日涨跌幅 <= 10
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

            # 筛选近五日涨跌幅 <= 10的
            for i in rule5_list:
                try:
                    code = i['code']
                    stock = code
                    name = self.yestoday_stock_dict[code]['name']
                    zdf = self.yestoday_stock_dict[code]['zdf']
                    jlr = self.yestoday_stock_dict[code]['jlr']
                    kdj_num = [ float(s['kdj'][0])>float(s['kdj'][1])  for s in self.stock_jx_data[code]].count(True)
                    #kdj_avg = (float(self.stock_jx_data[code][-1]['kdj'][0])+float(self.stock_jx_data[code][-1]['kdj'][1])+float(self.stock_jx_data[code][-1]['kdj'][2]))/3
                    #kdj_avg = "%.2f" % kdj_avg
                    kdj_avg = [ "%.2f" % (sum([float(ii) for ii in s['kdj']])/3) for s in self.stock_jx_data[code]]
                    kdj_avg = "%s->%s->%s->%s->%s" % (kdj_avg[0], kdj_avg[1], kdj_avg[2], kdj_avg[3], kdj_avg[4])
                    k = "%.2f" % float(self.stock_jx_data[code][-1]['kdj'][0])
                    d = "%.2f" % float(self.stock_jx_data[code][-1]['kdj'][1])
                    if i['zdf_5days'] > 0 and i['zdf_5days'] <= 20 \
                    and '流出' not in self.now_stock_dict[stock]['summary'] \
                    and ('质地不佳' not in self.now_stock_dict[stock]['summary'] or '质地很差' not in self.now_stock_dict[stock]['summary']) \
                    and 1==1:
                        fh = "\033[1;37m+\033[0m"
                        content = "[%s][%s][rule5][%s][%s][kdj_num:%s][kd:%s,%s][kdj_avg:%s] 昨日净流入:%s 昨日涨跌幅:%s 今日净流入:%s 今日涨跌幅:%s 近五净流入:%s万 近五涨跌幅:%s ma5:%s ma10:%s ma30:%s" % (fh, time.strftime('%Y-%m-%d %H:%M:%S' , time.localtime()), code, self.yestoday_stock_dict[stock]['name'], kdj_num, k, d, kdj_avg, jlr, zdf, self.now_stock_dict[stock]['jlr'], self.now_stock_dict[stock]['zdf'], self.yestoday_stock_dict[stock]['jlr_5days'], self.now_stock_dict[stock]['zdf_5d'], self.yestoday_stock_dict[stock]['ma5'], self.yestoday_stock_dict[stock]['ma10'], self.yestoday_stock_dict[stock]['ma30'])
                        if code not in self.rule_matched_list['rule5']:
                            if self.filter_bad(code):
                                print content
                                self.add2matched("rule5", code)
                                self.write_result("rule5", content)
                                self.rule_matched_list['rule5'].append(code)

                    else:
                        continue
                except Exception as e1:
                    #print(2, e1)
                    continue
        except Exception as e2:
            #print(3, e2)
            pass

        # 其他规则
        for stock in self.yestoday_stock_dict:
            try:
                code = self.yestoday_stock_dict[stock]['code']
                name = self.yestoday_stock_dict[stock]['name']
                zdf = self.yestoday_stock_dict[stock]['zdf']
                jlr = self.yestoday_stock_dict[stock]['jlr']

                """
                import pdb;pdb.set_trace()
                k>d
                k<60
                今日k>昨日k
                今日涨跌幅 < 8 self.stock_jx_data[code][-1]['changepercent']
                净流入>100w
                """
                kdj_num = [ float(s['kdj'][0])>float(s['kdj'][1])  for s in self.stock_jx_data[code]].count(True)
                #kdj_avg = (float(self.stock_jx_data[code][-1]['kdj'][0])+float(self.stock_jx_data[code][-1]['kdj'][1])+float(self.stock_jx_data[code][-1]['kdj'][2]))/3
                #kdj_avg = "%.2f" % kdj_avg
                kdj_avg = [ "%.2f" % (sum([float(ii) for ii in s['kdj']])/3) for s in self.stock_jx_data[code]]
                kdj_avg = "%s->%s->%s->%s->%s" % (kdj_avg[0], kdj_avg[1], kdj_avg[2], kdj_avg[3], kdj_avg[4])
                k = "%.2f" % float(self.stock_jx_data[code][-1]['kdj'][0])
                d = "%.2f" % float(self.stock_jx_data[code][-1]['kdj'][1])

                if float(self.stock_jx_data[code][-2]['kdj'][0]) < 70 \
                and float(self.stock_jx_data[code][-1]['kdj'][0]) > float(self.stock_jx_data[code][-1]['kdj'][1]) \
                and float(self.stock_jx_data[code][-1]['kdj'][0]) > float(self.stock_jx_data[code][-2]['kdj'][0]) \
                and self.stock_jx_data[code][-1]['changepercent'] < 8 \
                and float(self.yestoday_stock_dict[stock]['jlr_5days']) > 100 \
                and sum([ s['changepercent'] for s in self.stock_jx_data[code]]) < 15 \
                and sum([ s['changepercent'] for s in self.stock_jx_data[code]]) >= -5 \
                and 1==1:
                    fh = "\033[1;37m+\033[0m"
                    content = "[%s][%s][kdj][%s][%s][kdj_num:%s][kd:%s,%s][kdj_avg:%s] 昨日净流入:%s 昨日涨跌幅:%s 今日净流入:%s 今日涨跌幅:%s 近五净流入:%s万 近五涨跌幅:%s ma5:%s ma10:%s ma30:%s" % (fh, time.strftime('%Y-%m-%d %H:%M:%S' , time.localtime()), code, self.yestoday_stock_dict[stock]['name'], kdj_num, k, d, kdj_avg, jlr, zdf, self.now_stock_dict[stock]['jlr'], self.now_stock_dict[stock]['zdf'], self.yestoday_stock_dict[stock]['jlr_5days'], self.now_stock_dict[stock]['zdf_5d'], self.yestoday_stock_dict[stock]['ma5'], self.yestoday_stock_dict[stock]['ma10'], self.yestoday_stock_dict[stock]['ma30'])
                    if code not in self.rule_matched_list['kdj']:
                        if self.filter_bad(code):
                            print content
                            self.add2matched("kdj", code)
                            self.write_result("kdj", content)
                            self.rule_matched_list['kdj'].append(code)

                """
                : 超级反弹
                1. 前天k值小于10 且 前天k值大于0
                2. 昨日k值大于昨日d值

                """
                if float(self.stock_jx_data[code][-2]['kdj'][0]) < 10 and float(self.stock_jx_data[code][-2]['kdj'][0]) > 0 \
                and float(self.stock_jx_data[code][-1]['kdj'][0]) > float(self.stock_jx_data[code][-1]['kdj'][1]) \
                and 1==1:
                    content = "[%s][%s][super_kdj][%s][%s][kdj_num:%s][kd:%s,%s][kdj_avg:%s] 昨日净流入:%s 昨日涨跌幅:%s 今日净流入:%s 今日涨跌幅:%s 近五净流入:%s万 近五涨跌幅:%s ma5:%s ma10:%s ma30:%s" % (fh, time.strftime('%Y-%m-%d %H:%M:%S' , time.localtime()), code, self.yestoday_stock_dict[stock]['name'], kdj_num, k, d, kdj_avg, jlr, zdf, self.now_stock_dict[stock]['jlr'], self.now_stock_dict[stock]['zdf'], self.yestoday_stock_dict[stock]['jlr_5days'], self.now_stock_dict[stock]['zdf_5d'], self.yestoday_stock_dict[stock]['ma5'], self.yestoday_stock_dict[stock]['ma10'], self.yestoday_stock_dict[stock]['ma30'])
                    if code not in self.rule_matched_list['super_kdj']:
                        if self.filter_bad(code):
                            print content
                            self.add2matched("super_kdj", code)
                            self.write_result("super_kdj", content)
                            self.rule_matched_list['super_kdj'].append(code)
                            self.notify("异动首次命中规则, 快关注!!", content, True, code)

                #                          KDJ指标                  
                #- 昨日kdj
                _L_k = float(self.stock_jx_data[code][-1]['kdj'][0])
                _L_d = float(self.stock_jx_data[code][-1]['kdj'][1])
                _L_j = float(self.stock_jx_data[code][-1]['kdj'][2])
                _L_kj_diff = _L_k - _L_j
                _L_kd_diff = _L_k - _L_d
                _L_dj_diff = _L_d - _L_j
                _L_dk_diff = _L_d - _L_k

                # - 前日kdj
                _Y_k = float(self.stock_jx_data[code][-2]['kdj'][0])
                _Y_d = float(self.stock_jx_data[code][-2]['kdj'][1])
                _Y_j = float(self.stock_jx_data[code][-2]['kdj'][2])
                _Y_kj_diff = _Y_k - _Y_j
                _Y_kd_diff = _Y_k - _Y_d
                _Y_dj_diff = _Y_d - _Y_j
                _Y_dk_diff = _Y_d - _Y_k

                # rule7:kdj指标
                # j>k>d主升浪
                # j的1.68倍 > d
                # 昨天或者前天有一次j<k的
                if float(self.stock_jx_data[stock][-1]['kdj'][-1]) > float(self.stock_jx_data[stock][-1]['kdj'][0]) \
                    and float(self.stock_jx_data[stock][-1]['kdj'][-1]) > float(self.stock_jx_data[stock][-1]['kdj'][1]) \
                    and float(self.stock_jx_data[stock][-1]['kdj'][0]) > float(self.stock_jx_data[stock][-1]['kdj'][1]) \
                    and float(self.stock_jx_data[stock][-1]['kdj'][0]) > float(self.stock_jx_data[stock][-1]['kdj'][1]) \
                    and (float(self.stock_jx_data[stock][-2]['kdj'][-1]) < float(self.stock_jx_data[stock][-2]['kdj'][0]) or float(self.stock_jx_data[stock][-3]['kdj'][-1]) < float(self.stock_jx_data[stock][-3]['kdj'][0])) \
                    and float(self.stock_jx_data[stock][-1]['kdj'][-1])/1.68 > float(self.stock_jx_data[stock][-1]['kdj'][1]) \
                    and '流出' not in self.now_stock_dict[stock]['summary'] \
                    and 1==1:
                    #print "[%s][rule7][%s][%s] 昨日净流入:%s 昨日涨跌幅:%s 今日净流入:%s 今日涨跌幅:%s 近五净流入:%s万 近五涨跌幅:%s ma5:%s ma10:%s ma30:%s" % (time.strftime('%Y-%m-%d %H:%M:%S' , time.localtime()), code, self.yestoday_stock_dict[stock]['name'], jlr, zdf, self.now_stock_dict[stock]['jlr'], self.now_stock_dict[stock]['zdf'], self.yestoday_stock_dict[stock]['jlr_5days'], self.now_stock_dict[stock]['zdf_5d'], self.yestoday_stock_dict[stock]['ma5'], self.yestoday_stock_dict[stock]['ma10'], self.yestoday_stock_dict[stock]['ma30'])
                    fh = "\033[1;37m+\033[0m"
                    content = "[%s][%s][rule7][%s][%s][kdj_num:%s][kd:%s,%s][kdj_avg:%s] 昨日净流入:%s 昨日涨跌幅:%s 今日净流入:%s 今日涨跌幅:%s 近五净流入:%s万 近五涨跌幅:%s ma5:%s ma10:%s ma30:%s" % (fh, time.strftime('%Y-%m-%d %H:%M:%S' , time.localtime()), code, self.yestoday_stock_dict[stock]['name'], kdj_num, k, d, kdj_avg, jlr, zdf, self.now_stock_dict[stock]['jlr'], self.now_stock_dict[stock]['zdf'], self.yestoday_stock_dict[stock]['jlr_5days'], self.now_stock_dict[stock]['zdf_5d'], self.yestoday_stock_dict[stock]['ma5'], self.yestoday_stock_dict[stock]['ma10'], self.yestoday_stock_dict[stock]['ma30'])
                    if code not in self.rule_matched_list['rule7']:
                        if self.filter_bad(code):
                            print content
                            self.add2matched("rule7", code)
                            self.write_result("rule7", content)
                            self.rule_matched_list['rule7'].append(code)

                # rule8. kdj指标
                """
                前天
                k-d < 10

                昨天
                k-d > 0 and k-d < 10

                k < 50, k>d
                """
                # 昨日k-d值>0, 且昨日k-d值 < 20
                # 前天k值<30
                # 昨天k值<55
                # 前天k-d值<10
                # 前天k值小于d值
                if _L_kd_diff > 0 and _L_kd_diff < 20 \
                and float(_Y_k) < 30 \
                and float(_L_k) < 55 \
                and _Y_kd_diff < 10 \
                and _Y_k < _Y_d \
                and '流出' not in self.now_stock_dict[stock]['summary'] \
                and 1==1:
                    #print "[%s][rule8][%s][%s] 昨日净流入:%s 昨日涨跌幅:%s 今日净流入:%s 今日涨跌幅:%s 近五净流入:%s万 近五涨跌幅:%s ma5:%s ma10:%s ma30:%s" % (time.strftime('%Y-%m-%d %H:%M:%S' , time.localtime()), code, self.yestoday_stock_dict[stock]['name'], jlr, zdf, self.now_stock_dict[stock]['jlr'], self.now_stock_dict[stock]['zdf'], self.yestoday_stock_dict[stock]['jlr_5days'], self.now_stock_dict[stock]['zdf_5d'], self.yestoday_stock_dict[stock]['ma5'], self.yestoday_stock_dict[stock]['ma10'], self.yestoday_stock_dict[stock]['ma30'])
                    fh = "\033[1;37m+\033[0m"
                    content = "[%s][%s][rule8][%s][%s][kdj_num:%s][kd:%s,%s][kdj_avg:%s] 昨日净流入:%s 昨日涨跌幅:%s 今日净流入:%s 今日涨跌幅:%s 近五净流入:%s万 近五涨跌幅:%s ma5:%s ma10:%s ma30:%s" % (fh, time.strftime('%Y-%m-%d %H:%M:%S' , time.localtime()), code, self.yestoday_stock_dict[stock]['name'], kdj_num, k, d, kdj_avg, jlr, zdf, self.now_stock_dict[stock]['jlr'], self.now_stock_dict[stock]['zdf'], self.yestoday_stock_dict[stock]['jlr_5days'], self.now_stock_dict[stock]['zdf_5d'], self.yestoday_stock_dict[stock]['ma5'], self.yestoday_stock_dict[stock]['ma10'], self.yestoday_stock_dict[stock]['ma30'])
                    if code not in self.rule_matched_list['rule8']:
                        if self.filter_bad(code):
                            print content
                            self.add2matched("rule8", code)
                            self.write_result("rule8", content)
                            self.rule_matched_list['rule8'].append(code)
                            self.notify("异动首次命中规则, 快关注!!", content, True, code)

                # rule9. kdj+macd指标
                """
                1. macd < 0 且 macd > -0.1
                2. 近三天macd逐步上升
                3. 近三天kdj逐步缩小
                4. 今日k-d > -2
                5. k值<55
                """
                if float(self.stock_jx_data[stock][-1]['macd'][-1])<0 and float(self.stock_jx_data[stock][-1]['macd'][-1])>-0.1 \
                and float(self.stock_jx_data[stock][-1]['macd'][-1]) > float(self.stock_jx_data[stock][-1]['macd'][-2]) \
                and float(self.stock_jx_data[stock][-1]['macd'][-2]) > float(self.stock_jx_data[stock][-1]['macd'][-3]) \
                and float(self.stock_jx_data[stock][-1]['macd'][-1]) > float(self.stock_jx_data[stock][-1]['macd'][-3]) \
                and _L_kd_diff > -2 \
                and _L_k < 30 \
                and 1==1:
                    fh = "\033[1;37m+\033[0m"
                    content = "[%s][%s][rule9][%s][%s][kdj_num:%s][kd:%s,%s][kdj_avg:%s] 昨日净流入:%s 昨日涨跌幅:%s 今日净流入:%s 今日涨跌幅:%s 近五净流入:%s万 近五涨跌幅:%s ma5:%s ma10:%s ma30:%s" % (fh, time.strftime('%Y-%m-%d %H:%M:%S' , time.localtime()), code, self.yestoday_stock_dict[stock]['name'], kdj_num, k, d, kdj_avg, jlr, zdf, self.now_stock_dict[stock]['jlr'], self.now_stock_dict[stock]['zdf'], self.yestoday_stock_dict[stock]['jlr_5days'], self.now_stock_dict[stock]['zdf_5d'], self.yestoday_stock_dict[stock]['ma5'], self.yestoday_stock_dict[stock]['ma10'], self.yestoday_stock_dict[stock]['ma30'])
                    if code not in self.rule_matched_list['rule9']:
                        if self.filter_bad(code):
                            print content
                            self.add2matched("rule9", code)
                            self.write_result("rule9", content)
                            self.rule_matched_list['rule9'].append(code)
                            self.notify("异动首次命中规则, 快关注!!", content, True, code)

                # rule10. kdj超跌指标
                """
                1. 前天k<10
                2. 今天k>10

                1. 前天>10
                2. 昨天<10
                3. 当前zdf>0
                4. 当前jlr>0
                5. 昨日收盘在5日线
                """

                if _Y_k < 10 and _L_k > 10 \
                and self.stock_jx_data[stock][-1]['close'] > self.stock_jx_data[stock][-1]['ma5'] \
                and 1==1:
                    fh = "\033[1;37m+\033[0m"
                    content = "[%s][%s][rule10][%s][%s][kdj_num:%s][kd:%s,%s][kdj_avg:%s] 昨日净流入:%s 昨日涨跌幅:%s 今日净流入:%s 今日涨跌幅:%s 近五净流入:%s万 近五涨跌幅:%s ma5:%s ma10:%s ma30:%s" % (fh, time.strftime('%Y-%m-%d %H:%M:%S' , time.localtime()), code, self.yestoday_stock_dict[stock]['name'], kdj_num, k, d, kdj_avg, jlr, zdf, self.now_stock_dict[stock]['jlr'], self.now_stock_dict[stock]['zdf'], self.yestoday_stock_dict[stock]['jlr_5days'], self.now_stock_dict[stock]['zdf_5d'], self.yestoday_stock_dict[stock]['ma5'], self.yestoday_stock_dict[stock]['ma10'], self.yestoday_stock_dict[stock]['ma30'])
                    if code not in self.rule_matched_list['rule10']:
                        print content
                        self.add2matched("rule10", code)
                        self.write_result("rule10", content)
                        self.rule_matched_list['rule10'].append(code)
                elif _Y_k > 10 and _L_k < 10 \
                and float(self.now_stock_dict[code]['jlr']) > 0 \
                and float(self.now_stock_dict[code]['zdf']) > 0 \
                and self.now_stock_dict[code]['trade'] > self.stock_jx_data[code][-1]['ma5'] \
                and 1==1:
                    fh = "\033[1;37m+\033[0m"
                    content = "[%s][%s][rule10][%s][%s][kdj_num:%s][kd:%s,%s][kdj_avg:%s] 昨日净流入:%s 昨日涨跌幅:%s 今日净流入:%s 今日涨跌幅:%s 近五净流入:%s万 近五涨跌幅:%s ma5:%s ma10:%s ma30:%s" % (fh, time.strftime('%Y-%m-%d %H:%M:%S' , time.localtime()), code, self.yestoday_stock_dict[stock]['name'], kdj_num, k, d, kdj_avg, jlr, zdf, self.now_stock_dict[stock]['jlr'], self.now_stock_dict[stock]['zdf'], self.yestoday_stock_dict[stock]['jlr_5days'], self.now_stock_dict[stock]['zdf_5d'], self.yestoday_stock_dict[stock]['ma5'], self.yestoday_stock_dict[stock]['ma10'], self.yestoday_stock_dict[stock]['ma30'])
                    if code not in self.rule_matched_list['rule10']:
                        print content
                        self.add2matched("rule10", code)
                        self.write_result("rule10", content)
                        self.rule_matched_list['rule10'].append(code)
                        self.notify("异动首次命中规则, 快关注!!", content, True, code)

            except Exception as e:
                try:
                    if 'list index out of range' in e:
                        return

                    # 如果是int，则不告警
                    code = int(str(e).strip("'"))
                except:
                    pass
                    print("[-][%s] 规则过滤出错!! errcode:100207, errmsg:%s" % (stock, e))
                    #import pdb;pdb.set_trace()

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

                ntime = "\033[1;32m%s\033[0m" % str(time.strftime('%H:%M:%S' , time.localtime()))
                fh = "\033[1;37m+\033[0m"
                print("[%s][%s] 发现当天均线缓存文件, 加载中请稍后..." % (fh, ntime))
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
                    #url = "http://api.finance.ifeng.com/akdaily/?code=%s&type=last" % self.all_stock_code[i]['ts_code']
                    #url = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData?symbol=%s&scale=240&datalen=5" % self.all_stock_code[i]['ts_code']
                    url = "http://quote.eastmoney.com/zixuan/api/znzg?code=%s" % self.all_stock_code[i]['code']
                    threads.append(p.spawn(self.jx_data_func, url, self.all_stock_code[i]['ts_code'], self.all_stock_code[i]['code']))

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

    def format_realtime_data(self):
        # 首先获取主力成本、排名、得分、控盘形态、最新价格、最新涨跌幅
        for code in self.stock_anaylse_dict.keys():
            if code not in self.now_format_stock_dict.keys():
                self.now_format_stock_dict[code] = {}
                self.now_format_stock_dict[code]['code'] = code                                                     # 股票代码
                self.now_format_stock_dict[code]['name'] = self.stock_anaylse_dict[code]['name']                    # 股票名称
                self.now_format_stock_dict[code]['kpType'] = self.stock_anaylse_dict[code]['kpzt']                  # 控盘情况
                self.now_format_stock_dict[code]['zlcb'] = self.stock_anaylse_dict[code]['zlcb']                    # 主力成本
                self.now_format_stock_dict[code]['zjdx1'] = self.stock_anaylse_dict[code]['zjdx1']                  # 主力资金流入状态
                self.now_format_stock_dict[code]['hydx1'] = self.stock_anaylse_dict[code]['hydx1']                  # 行业资金流入状态
                self.now_format_stock_dict[code]['score'] = self.stock_anaylse_dict[code]['TotalScore']             # 得分
                self.now_format_stock_dict[code]['rank'] = self.stock_anaylse_dict[code]['Ranking']                 # 排名
                self.now_format_stock_dict[code]['zl_ma20'] = self.stock_anaylse_dict[code]['zl_ma20']              # 主力成本60日线
                self.now_format_stock_dict[code]['zl_ma60'] = self.stock_anaylse_dict[code]['zl_ma60']              # 主力成本20日线
                self.now_format_stock_dict[code]['focus'] = self.stock_anaylse_dict[code]['FocusScore']             # 关注度
                self.now_format_stock_dict[code]['rankup'] = self.stock_anaylse_dict[code]['rankup']                # 近期排行上升还是下降?
                self.now_format_stock_dict[code]['summary'] = self.stock_anaylse_dict[code]['summary']              # 总结
                self.now_format_stock_dict[code]['value_summary'] = self.stock_anaylse_dict[code]['value_summary']  # 价值总结
                self.now_format_stock_dict[code]['hypp'] = self.stock_anaylse_dict[code]['ValueRanking']            # 行业排名
                self.now_format_stock_dict[code]['cjjj'] = self.stock_anaylse_dict[code]['ActivePrice']             # 成交活跃价格
                self.now_format_stock_dict[code]['sccb'] = self.stock_anaylse_dict[code]['AvgBuyPrice']             # 市场成本
                self.now_format_stock_dict[code]['crsl'] = self.stock_anaylse_dict[code]['RisePro']                 # 次日胜率
                self.now_format_stock_dict[code]['drbx'] = self.stock_anaylse_dict[code]['LeadPre']                 # 当日表现
                self.now_format_stock_dict[code]['ylw'] = self.stock_anaylse_dict[code]['PressurePosition']         # 压力位
                self.now_format_stock_dict[code]['zcw'] = self.stock_anaylse_dict[code]['SupportPosition']          # 支撑位
                self.now_format_stock_dict[code]['cyyy'] = self.stock_anaylse_dict[code]['cyyy_zdf']                # 参与意愿
                self.now_format_stock_dict[code]['pjyk'] = self.stock_anaylse_dict[code]['pjyk_zdf']                # 平均盈亏
                self.now_format_stock_dict[code]['yk60r_zdf'] = self.stock_anaylse_dict[code]['yk60r_zdf']          # 60r盈亏
                self.now_format_stock_dict[code]['yestoday_jlr'] = self.stock_anaylse_dict[code]['yestoday_jlr']    # 昨日净流入
                self.now_format_stock_dict[code]['yestoday_zdf'] = self.stock_anaylse_dict[code]['yestoday_zdf']    # 昨日涨跌幅
                self.now_format_stock_dict[code]['jlr_5days'] = self.stock_anaylse_dict[code]['jlr_5days']          # 5日净流入

        # 获取当日股票数据(所属行业、主力实时排名)
        url = "http://push2.eastmoney.com/api/qt/clist/get?cb=&fid=f184&po=1&pz=5000&pn=1&np=1&fltt=2&invt=2&fields=f2,f3,f12,f13,f14,f62,f184,f225,f165,f263,f109,f175,f264,f160,f100,f124,f265&ut=b2884a393a59ad64002292a3e90d46a5&fs=m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,m:1+t:23+f:!2,m:0+t:7+f:!2,m:1+t:3+f:!2"
        data = self.s.get(url, timeout=5).json()['data']['diff']
        for stock in data:
            try:
                code = stock['f12']                 # 股票代码
                industry = stock['f100']            # 所属行业
                zlrank_today = stock['f225']        # 今日排名
                zlrank_5d = stock['f263']           # 五日主力排名
                zdf_5d = stock['f109']              # 近五日涨跌幅
                zlrannk_10d = stock['f264']         # 十日主力排名
                zdf_10d = stock['f160']             # 近十日涨跌幅

                if code in self.now_format_stock_dict.keys():
                    self.now_format_stock_dict[code]['industry'] = industry
                    self.now_format_stock_dict[code]['zlrank_today'] = zlrank_today
                    self.now_format_stock_dict[code]['zlrank_5d'] = zlrank_5d
                    self.now_format_stock_dict[code]['zdf_5d'] = zdf_5d
                    self.now_format_stock_dict[code]['zlrannk_10d'] = zlrannk_10d
                    self.now_format_stock_dict[code]['zdf_10d'] = zdf_10d
            except:
                continue

        # 获取当日股票数据(最新价、涨跌幅、资金资金实时流入情况)
        url = "http://push2.eastmoney.com/api/qt/clist/get?cb=&fid=f62&po=1&pz=5000&pn=1&np=1&fltt=2&invt=2&ut=b2884a393a59ad64002292a3e90d46a5&fs=m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,m:1+t:23+f:!2,m:0+t:7+f:!2,m:1+t:3+f:!2&fields=f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f204,f205,f124"
        data = self.s.get(url, timeout=5).json()['data']['diff']
        for stock in data:
            try:
                code = stock['f12']                 # 股票代码
                name = stock['f14']                 # 股票名称
                trade = stock['f2']                 # 最新价
                zdf = stock['f3']                   # 涨跌幅
                jlr = stock['f62']/10000                  # 主力净流入
                cddjlr = stock['f66']/10000               # 超大单净流入
                ddjlr = stock['f72']/10000                # 大单净流入
                zdjlr = stock['f78']/10000                # 中单净流入
                xdjlr = stock['f84']/10000                # 小单净流入

                if code in self.now_format_stock_dict.keys():
                    self.now_format_stock_dict[code]['zdf'] = zdf
                    self.now_format_stock_dict[code]['jlr'] = jlr
                    self.now_format_stock_dict[code]['trade'] = trade
                    self.now_format_stock_dict[code]['cddjlr'] = cddjlr
                    self.now_format_stock_dict[code]['ddjlr'] = ddjlr
                    self.now_format_stock_dict[code]['zdjlr'] = zdjlr
                    self.now_format_stock_dict[code]['xdjlr'] = xdjlr
            except:
                continue

        # 获取当日股票市值
        url = "http://18.push2.eastmoney.com/api/qt/clist/get?cb=&pn=1&pz=10000&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2&invt=2&fid=f3&fs=m:0+t:6,m:0+t:13,m:0+t:80,m:1+t:2,m:1+t:23&fields=f12,f14,f20"
        data = self.s.get(url, timeout=5).json()['data']['diff']
        for stock in data:
            try:
                code = stock['f12']                         # 股票代码
                name = stock['f14']                         # 股票名称
                volume = float(stock['f20'])/100000000       # 市值

                if code in self.now_format_stock_dict.keys():
                    self.now_format_stock_dict[code]['volume'] = volume
            except:
                continue

    def filter_conditions(self, single, code):
        if self.conditions_filter is False:
            return True
        else:
            # ----------- 条件过滤 ---------
            #                          KDJ指标                  
            #- 昨日kdj
            _L_k = float(self.stock_jx_data[code][-1]['kdj'][0])
            _L_d = float(self.stock_jx_data[code][-1]['kdj'][1])
            _L_j = float(self.stock_jx_data[code][-1]['kdj'][2])
            _L_kj_diff = _L_k - _L_j
            _L_kd_diff = _L_k - _L_d
            _L_dj_diff = _L_d - _L_j
            _L_dk_diff = _L_d - _L_k

            # - 前日kdj
            _Y_k = float(self.stock_jx_data[code][-2]['kdj'][0])
            _Y_d = float(self.stock_jx_data[code][-2]['kdj'][1])
            _Y_j = float(self.stock_jx_data[code][-2]['kdj'][2])
            _Y_kj_diff = _Y_k - _Y_j
            _Y_kd_diff = _Y_k - _Y_d
            _Y_dj_diff = _Y_d - _Y_j
            _Y_dk_diff = _Y_d - _Y_k


            # 昨日
            # d j  > 0 < 10 (越小越好)
            # d k  > 0 < 10
            if _L_dj_diff > 0 and _L_dj_diff < 10 \
            and _L_dk_diff > 0 and _L_dk_diff < 10 \
            and _Y_dj_diff < _Y_dk_diff \
            and self.stock_jx_data[code][-1]['changepercent'] > 0 \
            and 1==1:
                import pdb;pdb.set_trace()
                return True

            #import pdb;pdb.set_trace()

            # 前一日
            #d<j d<k


            # 1. 行业流入:增仓
            """
            #if _L_k_diff >= 0 \
            #and _Y_d > _Y_k and _Y_d > _Y_j \
            #and _L_k > _L_d and _L_j > _L_k \
            if self.now_format_stock_dict[code]['hydx1'] == 1 \
            and 1==1:
                #import pdb;pdb.set_trace()
                return True
            """

    def format_func(self, result_file):

        # 第一步、通过文件获取命中规则的股票代码列表
        with open(result_file, 'r') as f:
            result = f.read()

        sort_code_list = {}
        for stock in result.split("\n"):
            try:
                code = stock.split("[")[3].strip("]")
                if code in sort_code_list.keys():
                    sort_code_list[code].append(stock)
                else:
                    sort_code_list[code] = []
                    sort_code_list[code].append(stock)
            except:
                continue

        # 第二步、加载数据分析缓存文件
        self.get_anaylse_data()

        # 第三步、获取所有股票实时净流入等信息
        self.format_realtime_data()

        # 第四步、分析命中规则的股票代码信息
        all_stock_list = []
        sort_code_dict = {}
        for code in sort_code_list.keys():
            try:
                # 资金状态
                single = 0

                if code in self.now_format_stock_dict.keys():
                    # 首个净流入计算
                    fst_jlr = float(sort_code_list[code][0].split("[")[7].split("]")[0].split(":")[1].strip('万'))

                    if float(self.now_format_stock_dict[code]['jlr']) < 0:
                        if fst_jlr > 0:
                            note = '流出状态'
                            single = 0
                        else:
                            if abs(float(self.now_format_stock_dict[code]['jlr'])) > fst_jlr:
                                note = '流出状态'
                                single = 0
                            else:
                                note = '流入状态'
                                single = 1
                    else:
                        if float(self.now_format_stock_dict[code]['jlr']) > fst_jlr:
                            note = "流入状态"
                            single = 1
                        else:
                            note = "流出状态"
                            single = 0

                    # 增加过滤条件
                    _is_filter = False
                    if self.filter_conditions(single, code):
                        now_money_flow_bs = self.money_flow_calc(fst_jlr, self.now_format_stock_dict[code]['jlr'])
                        # 只过滤流入状态的.
                        if single == 1 or self.format_all is True:
                            # 现价
                            if self.now_format_stock_dict[code]['trade'] >= self.now_format_stock_dict[code]['zlcb']:
                                now_trade = "\033[1;34m%s\033[0m" % self.now_format_stock_dict[code]['trade']
                            else:
                                now_trade = "\033[1;31m%s\033[0m" % self.now_format_stock_dict[code]['trade']

                            # 涨跌幅
                            if self.now_format_stock_dict[code]['zdf'] < 0:
                                now_zdf = "\033[1;34m%.2f%%\033[0m" % self.now_format_stock_dict[code]['zdf']
                            else:
                                now_zdf = "\033[1;31m%.2f%%\033[0m" % self.now_format_stock_dict[code]['zdf']

                            # 得分
                            if self.now_format_stock_dict[code]['score'] >= 75:
                                score = "\033[1;31m%s\033[0m" % self.now_format_stock_dict[code]['score']
                            else:
                                score = "\033[1;34m%s\033[0m" % self.now_format_stock_dict[code]['score']

                            # 排名
                            if float(self.now_format_stock_dict[code]['rank']) > 200:
                                rank = "\033[1;34m%s\033[0m" % self.now_format_stock_dict[code]['rank']
                            else:
                                rank = "\033[1;31m%s\033[0m" % self.now_format_stock_dict[code]['rank']

                            # 排名增长
                            if self.now_format_stock_dict[code]['rankup'] == '-':
                                pass
                            elif float(self.now_format_stock_dict[code]['rankup']) < 0:
                                rankup = "\033[1;34m%s\033[0m" % self.now_format_stock_dict[code]['rankup']                            
                            else:
                                rankup = "\033[1;31m%s\033[0m" % self.now_format_stock_dict[code]['rankup']

                            # 主力成本20日与60日涨跌幅
                            try:
                                zl_20to60 = "%.2f" % ((self.now_format_stock_dict[code]['zl_ma20'] - self.now_format_stock_dict[code]['zl_ma60'] ) / self.now_format_stock_dict[code]['zl_ma60'] * 100)
                            except:
                                zl_20to60 = "0.00"
                            if float(zl_20to60) > 0:
                                zl_20to60 = "\033[1;31m%s%%\033[0m" % zl_20to60
                            else:
                                zl_20to60 = "\033[1;34m%s%%\033[0m" % zl_20to60

                            # 主力成本现在与60日涨跌幅
                            try:
                                zl_nowto20 = "%.2f" % ((self.now_format_stock_dict[code]['zlcb'] - self.now_format_stock_dict[code]['zl_ma20'] ) / self.now_format_stock_dict[code]['zl_ma20'] * 100)
                            except:
                                zl_nowto20 = "0.00"
                            if float(zl_nowto20) > 0:
                                zl_nowto20 = "\033[1;31m%s%%\033[0m" % zl_nowto20
                            else:
                                zl_nowto20 = "\033[1;34m%s%%\033[0m" % zl_nowto20

                            # 现价与主力成本涨跌幅
                            try:
                                now2zlcb = "%.2f" % ((self.now_format_stock_dict[code]['trade'] - self.now_format_stock_dict[code]['zlcb'] ) / self.now_format_stock_dict[code]['zlcb'] * 100)
                            except:
                                now2zlcb = "0.00"
                            if float(now2zlcb) > 0:
                                now2zlcb = "\033[1;31m%s%%\033[0m" % now2zlcb
                            else:
                                now2zlcb = "\033[1;34m%s%%\033[0m" % now2zlcb

                            # 增长倍数
                            if float(now_money_flow_bs) > 2.5:
                                now_money_flow_bs = "\033[1;31m%.2f\033[0m" % now_money_flow_bs
                            else:
                                now_money_flow_bs = "\033[1;33m%.2f\033[0m" % now_money_flow_bs

                            # 当前净流入
                            if self.now_format_stock_dict[code]['jlr'] > 1000:
                                now_jlr = "\033[1;31m%.2f\033[0m" % self.now_format_stock_dict[code]['jlr']
                            elif self.now_format_stock_dict[code]['jlr'] >= 500:
                                now_jlr = "\033[1;33m%.2f\033[0m" % self.now_format_stock_dict[code]['jlr']
                            elif self.now_format_stock_dict[code]['jlr'] <= 100:
                                now_jlr = "\033[1;32m%.2f\033[0m" % self.now_format_stock_dict[code]['jlr']
                            else:
                                now_jlr = self.now_format_stock_dict[code]['jlr']

                            # 主力排名
                            if self.now_format_stock_dict[code]['zlrank_today'] < self.now_format_stock_dict[code]['zlrank_5d'] and self.now_format_stock_dict[code]['zlrank_today'] < self.now_format_stock_dict[code]['zlrannk_10d']:
                                zlrank_today = "\033[1;31m%s\033[0m" % self.now_format_stock_dict[code]['zlrank_today']
                            elif self.now_format_stock_dict[code]['zlrank_today'] < self.now_format_stock_dict[code]['zlrank_5d']:
                                zlrank_today = "\033[1;33m%s\033[0m" % self.now_format_stock_dict[code]['zlrank_today']
                            else:
                                zlrank_today = self.now_format_stock_dict[code]['zlrank_today']

                            # 资金动向
                            if self.now_format_stock_dict[code]['zjdx1'] == 1:
                                zjdx1 = "\033[1;31m增仓\033[0m"
                            elif self.now_format_stock_dict[code]['zjdx1'] == 0:
                                zjdx1 = "\033[1;32m减仓\033[0m"
                            else:
                                zjdx1 = "中立"

                            # 行业动向
                            if self.now_format_stock_dict[code]['hydx1'] == 1:
                                hydx1 = "\033[1;31m增仓\033[0m"
                            elif self.now_format_stock_dict[code]['hydx1'] == 0:
                                hydx1 = "\033[1;32m减仓\033[0m"
                            else:
                                hydx1 = "中立"

                            # 今日打败
                            if float(self.now_format_stock_dict[code]['drbx']) >= 95:
                                drbx = "\033[1;31m%.2f\033[0m" % float(self.now_format_stock_dict[code]['drbx'])
                            else:
                                drbx = float(self.now_format_stock_dict[code]['drbx'])

                            # 上涨概率
                            if float(self.now_format_stock_dict[code]['crsl']) >= 48:
                                crsl = "\033[1;31m%.2f\033[0m" % float(self.now_format_stock_dict[code]['crsl'])
                            else:
                                crsl = "%.2f" % float(self.now_format_stock_dict[code]['crsl'])

                            # 市场关注度
                            if self.now_format_stock_dict[code]['focus'] >= 85:
                                focus = "\033[1;31m%.2f\033[0m" % float(self.now_format_stock_dict[code]['focus'])
                            else:
                                focus = "%.2f" % float(self.now_format_stock_dict[code]['focus'])

                            # 参与意愿
                            if self.now_format_stock_dict[code]['cyyy'] > 0:
                                cyyy = "\033[1;31m%.2f\033[0m" % float(self.now_format_stock_dict[code]['cyyy'])
                            else:
                                cyyy = "%.2f" % float(self.now_format_stock_dict[code]['cyyy'])

                            # 平均盈亏
                            if self.now_format_stock_dict[code]['pjyk'] <= 0:
                                pjyk = "\033[1;31m%.2f\033[0m" % float(self.now_format_stock_dict[code]['pjyk'])
                            else:
                                pjyk = "%.2f" % self.now_format_stock_dict[code]['pjyk']

                            # 60日盈亏
                            if self.now_format_stock_dict[code]['yk60r_zdf'] <= 20:
                                yk60r_zdf = "\033[1;31m%.2f\033[0m" % float(self.now_format_stock_dict[code]['yk60r_zdf'])
                            else:
                                yk60r_zdf = "%.2f" % self.now_format_stock_dict[code]['yk60r_zdf']

                            if '流出' in note:
                                # 第一行: 市场分析
                                msg = "[%s][%s][市场分析] 得分:%s, 昨日市场排名:%s[%s], 打败 %s 的股票, 今日上涨概率:%s , 市场关注度:%s 参与意愿:%s 平均盈亏:%s 控盘:%s 资金动向:%s 行业动向:%s\n" % (
                                                                                                                        code, \
                                                                                                                        self.now_format_stock_dict[code]['name'], \
                                                                                                                        score, \
                                                                                                                        rank, \
                                                                                                                        rankup, \
                                                                                                                        drbx, \
                                                                                                                        crsl, \
                                                                                                                        focus, \
                                                                                                                        cyyy, \
                                                                                                                        pjyk, \
                                                                                                                        self.now_format_stock_dict[code]['kpType'], \
                                                                                                                        zjdx1, \
                                                                                                                        hydx1

                                )

                                # 第二行: 涨跌状况
                                msg += "[%s][%s][涨跌状况] 价格(现/主/市)[%s/%.2f/%.2f][%s] 净流入:%s万, 近期涨跌幅(5/10/60):%s/%s/%s, now2zlcb:%s zl_nowto20:%s zl_ma20to60:%s,  资金呈 \033[1;32m%s\033[0m, 流出倍数:%s\n" % (
                                                                                                                        code, \
                                                                                                                        self.now_format_stock_dict[code]['name'], \
                                                                                                                        now_trade, \
                                                                                                                        float(self.now_format_stock_dict[code]['zlcb']), \
                                                                                                                        float(self.now_format_stock_dict[code]['sccb']), \
                                                                                                                        now_zdf, \
                                                                                                                        now_jlr, \
                                                                                                                        self.now_format_stock_dict[code]['zdf_5d'], \
                                                                                                                        self.now_format_stock_dict[code]['zdf_10d'], \
                                                                                                                        yk60r_zdf, \
                                                                                                                        now2zlcb, \
                                                                                                                        zl_nowto20, \
                                                                                                                        zl_20to60, \
                                                                                                                        note, \
                                                                                                                        now_money_flow_bs
                                )
                                msg += "[%s][%s][基本面] %s\n" % (code, self.now_format_stock_dict[code]['name'], self.now_format_stock_dict[code]['value_summary'])
                                msg += "[%s][%s][资金面] %s" % (code, self.now_format_stock_dict[code]['name'], self.now_format_stock_dict[code]['summary'])

                                """
                                msg = "[%s][%s][%s][%s/%.2f][%s] 当前净流入:%s万 得分:%s 排名:%s 资金排名(1/5/10):%s/%s/%s 近期涨跌幅(5/10):%s/%s now2zlcb:%s zl_nowto20:%s zl_ma20to60:%s 自首次监测到异动，资金呈 \033[1;34m%s\033[0m, 流出倍数:%s" % ( 
                                                                                                                        code, \
                                                                                                                        self.now_format_stock_dict[code]['name'], \
                                                                                                                        now_zdf, \
                                                                                                                        now_trade, \
                                                                                                                        self.now_format_stock_dict[code]['zlcb'], \
                                                                                                                        self.now_format_stock_dict[code]['kpType'], \
                                                                                                                        now_jlr, \
                                                                                                                        score, \
                                                                                                                        rank, \
                                                                                                                        zlrank_today, \
                                                                                                                        self.now_format_stock_dict[code]['zlrank_5d'], \
                                                                                                                        self.now_format_stock_dict[code]['zlrannk_10d'], \
                                                                                                                        self.now_format_stock_dict[code]['zdf_5d'], \
                                                                                                                        self.now_format_stock_dict[code]['zdf_10d'], \
                                                                                                                        score, \
                                                                                                                        rank, \
                                                                                                                        now2zlcb, \
                                                                                                                        zl_nowto20, \
                                                                                                                        zl_20to60, \
                                                                                                                        note, \
                                                                                                                        now_money_flow_bs
                                )
                                """
                            else:
                                # 第一行: 市场分析
                                msg = "[%s][%s][市场分析] 得分:%s, 昨日市场排名:%s[%s], 打败 %s 的股票, 今日上涨概率:%s , 市场关注度:%s 参与意愿:%s 平均盈亏:%s 控盘:%s 资金动向:%s 行业动向:%s\n" % (
                                                                                                                        code, \
                                                                                                                        self.now_format_stock_dict[code]['name'], \
                                                                                                                        score, \
                                                                                                                        rank, \
                                                                                                                        rankup, \
                                                                                                                        drbx, \
                                                                                                                        crsl, \
                                                                                                                        focus, \
                                                                                                                        cyyy, \
                                                                                                                        pjyk, \
                                                                                                                        self.now_format_stock_dict[code]['kpType'], \
                                                                                                                        zjdx1, \
                                                                                                                        hydx1

                                )

                                # 第二行: 涨跌状况
                                msg += "[%s][%s][涨跌状况] 价格(现/主/市)[%s/%.2f/%.2f][%s] 净流入:%s万, 近期涨跌幅(5/10/60):%s/%s/%s, now2zlcb:%s zl_nowto20:%s zl_ma20to60:%s,  资金呈 \033[1;31m%s\033[0m, 增长倍数:%s\n" % (
                                                                                                                        code, \
                                                                                                                        self.now_format_stock_dict[code]['name'], \
                                                                                                                        now_trade, \
                                                                                                                        float(self.now_format_stock_dict[code]['zlcb']), \
                                                                                                                        float(self.now_format_stock_dict[code]['sccb']), \
                                                                                                                        now_zdf, \
                                                                                                                        now_jlr, \
                                                                                                                        self.now_format_stock_dict[code]['zdf_5d'], \
                                                                                                                        self.now_format_stock_dict[code]['zdf_10d'], \
                                                                                                                        yk60r_zdf, \
                                                                                                                        now2zlcb, \
                                                                                                                        zl_nowto20, \
                                                                                                                        zl_20to60, \
                                                                                                                        note, \
                                                                                                                        now_money_flow_bs
                                )
                                msg += "[%s][%s][基本面] %s\n" % (code, self.now_format_stock_dict[code]['name'], self.now_format_stock_dict[code]['value_summary'])
                                msg += "[%s][%s][资金面] %s" % (code, self.now_format_stock_dict[code]['name'], self.now_format_stock_dict[code]['summary'])

                                """
                                msg = "[%s][%s][%s][%s/%.2f][%s] 当前净流入:%s万 得分:%s 排名:%s 资金排名(1/5/10):%s/%s/%s 近期涨跌幅(5/10):%s/%s now2zlcb:%s zl_nowto20:%s zl_ma20to60:%s 自首次监测到异动，资金呈 \033[1;31m%s\033[0m, 增长倍数:%s" % (
                                                                                                                        code, \
                                                                                                                        self.now_format_stock_dict[code]['name'], \
                                                                                                                        now_zdf, \
                                                                                                                        now_trade, \
                                                                                                                        self.now_format_stock_dict[code]['zlcb'], \
                                                                                                                        self.now_format_stock_dict[code]['kpType'], \
                                                                                                                        now_jlr, \
                                                                                                                        score, \
                                                                                                                        rank, \
                                                                                                                        zlrank_today, \
                                                                                                                        self.now_format_stock_dict[code]['zlrank_5d'], \
                                                                                                                        self.now_format_stock_dict[code]['zlrannk_10d'], \
                                                                                                                        self.now_format_stock_dict[code]['zdf_5d'], \
                                                                                                                        self.now_format_stock_dict[code]['zdf_10d'], \
                                                                                                                        now2zlcb, \
                                                                                                                        zl_nowto20, \
                                                                                                                        zl_20to60, \
                                                                                                                        note, \
                                                                                                                        now_money_flow_bs)
                                """
                            print msg
                            print "-"*150
                            _is_filter = True
                else:
                    print code

                for stock in sort_code_list[code]:
                    if (single == 1 or self.format_all is True) and _is_filter:
                        if '出现大幅流入' in stock:
                            stock = "\033[1;34m%s\033[0m" % stock
                            print stock
                        else:
                            stock = "\033[1;32m%s\033[0m" % stock
                            print stock

                if (single == 1 or self.format_all is True) and _is_filter:
                    line = "\033[1;31m_\033[0m"
                    print line*150
            except Exception as e:
                print(e)
                #import pdb;pdb.set_trace()
                continue
        print "\n"

    def format_result(self, result_file, parser):
        if not os.path.exists(result_file):
            print("[-] File not found!")
            parser.print_help()

        if self.format_loop:
            while True:
                self.format_func(result_file)
                time.sleep(10)
                os.system("clear")
        else:
            self.format_func(result_file)
            sys.exit()

    def add2zx_func(self, result_file, gid):
        with open(result_file, 'r') as f:
            result = f.read()

        sort_code_list = {}
        for stock in result.split("\n"):
            try:
                code = stock.split("[")[3].strip("]")
                if code in sort_code_list.keys():
                    sort_code_list[code].append(stock)
                else:
                    sort_code_list[code] = []
                    sort_code_list[code].append(stock)
            except:
                continue

        for code in sort_code_list.keys():
            fst_jlr = float(sort_code_list[code][0].split("[")[7].split("]")[0].split(":")[1].strip('万'))

            # 流出状态的不入库.
            if float(self.now_format_stock_dict[code]['jlr']) < 0:
                if fst_jlr > 0:
                    note = '流出状态'
                    single = 0
                else:
                    if abs(float(self.now_format_stock_dict[code]['jlr'])) > fst_jlr:
                        note = '流出状态'
                        single = 0
                    else:
                        note = '流入状态'
                        single = 1
            else:
                if float(self.now_format_stock_dict[code]['jlr']) > fst_jlr:
                    note = "流入状态"
                    single = 1
                else:
                    note = "流出状态"
                    single = 0

            # 质地很差不入库
            if '质地很差' in self.now_format_stock_dict[code]['value_summary']:
                return False

            if code.startswith("300") or code.startswith("00"):
                secid = 0
            elif code.startswith("68"):
                continue
            else:
                secid = 1

            if single == 1:
                url = "http://myfavor.eastmoney.com/v4/webouter/as?appkey=%s&cb=&g=%s&sc=%s$%s&_=1612340046932" % (self.appkey, gid, secid, code)
                print self.ds.get(url, timeout=10).text

    def add2zx(self, result_file):
        if not os.path.exists(result_file):
            print("[-] File not found!")
            parser.print_help()


        # 第二步、加载数据分析缓存文件
        self.get_anaylse_data()

        # 第三步、获取所有股票实时净流入等信息
        self.format_realtime_data()

        # 首先返回组列表
        _ginfolist = self.ginfolist()

        # 判断是否存在以当天时间命名的自选组
        today = time.strftime('%Y%m%d' , time.localtime())
        if today in _ginfolist.keys():
            gid = _ginfolist[today]
        else:
            gid = self.add_group(today)

        self.add2zx_func(result_file, gid)

    # 新增自选组
    def add_group(self, group):
        # 如果不存在, 则创建, 并返回group_id
        url = "http://myfavor.eastmoney.com/v4/webouter/ag?appkey=%s&cb=&gn=%s&_=1612340046939" % (self.appkey, group)
        result = self.ds.get(url, timeout=5).json()
        return result['data']['gid']

    # 返回组列表
    def ginfolist(self):
        url = "http://myfavor.eastmoney.com/v4/webouter/ggdefstkindexinfos?appkey=%s&cb=" % self.appkey
        group_info = self.ds.get(url, timeout=5).json()
        g_list = group_info['data']['ginfolist']
        g_dict = {}
        for g in g_list:
            g_dict[g['gname']] = g['gid']

        return g_dict

    def print_stock_info(self, code):
        codes = code.split(',')
        if codes:
            for code in codes:
                try:
                    # 现价
                    if self.now_format_stock_dict[code]['trade'] >= self.now_format_stock_dict[code]['zlcb']:
                        now_trade = "\033[1;34m%s\033[0m" % self.now_format_stock_dict[code]['trade']
                    else:
                        now_trade = "\033[1;31m%s\033[0m" % self.now_format_stock_dict[code]['trade']

                    # 涨跌幅
                    if self.now_format_stock_dict[code]['zdf'] < 0:
                        now_zdf = "\033[1;34m%.2f%%\033[0m" % self.now_format_stock_dict[code]['zdf']
                    else:
                        now_zdf = "\033[1;31m%.2f%%\033[0m" % self.now_format_stock_dict[code]['zdf']

                    # 得分
                    if self.now_format_stock_dict[code]['score'] >= 75:
                        score = "\033[1;31m%s\033[0m" % self.now_format_stock_dict[code]['score']
                    else:
                        score = "\033[1;34m%s\033[0m" % self.now_format_stock_dict[code]['score']

                    # 排名
                    if float(self.now_format_stock_dict[code]['rank']) > 200:
                        rank = "\033[1;34m%s\033[0m" % self.now_format_stock_dict[code]['rank']
                    else:
                        rank = "\033[1;31m%s\033[0m" % self.now_format_stock_dict[code]['rank']

                    # 排名增长
                    if self.now_format_stock_dict[code]['rankup'] == '-':
                        pass
                    elif float(self.now_format_stock_dict[code]['rankup']) < 0:
                        rankup = "\033[1;34m%s\033[0m" % self.now_format_stock_dict[code]['rankup']                            
                    else:
                        rankup = "\033[1;31m%s\033[0m" % self.now_format_stock_dict[code]['rankup']

                    # 主力成本20日与60日涨跌幅
                    try:
                        zl_20to60 = "%.2f" % ((self.now_format_stock_dict[code]['zl_ma20'] - self.now_format_stock_dict[code]['zl_ma60'] ) / self.now_format_stock_dict[code]['zl_ma60'] * 100)
                    except:
                        zl_20to60 = "0.00"
                    if float(zl_20to60) > 0:
                        zl_20to60 = "\033[1;31m%s%%\033[0m" % zl_20to60
                    else:
                        zl_20to60 = "\033[1;34m%s%%\033[0m" % zl_20to60

                    # 主力成本现在与60日涨跌幅
                    try:
                        zl_nowto20 = "%.2f" % ((self.now_format_stock_dict[code]['zlcb'] - self.now_format_stock_dict[code]['zl_ma20'] ) / self.now_format_stock_dict[code]['zl_ma20'] * 100)
                    except:
                        zl_nowto20 = "0.00"
                    if float(zl_nowto20) > 0:
                        zl_nowto20 = "\033[1;31m%s%%\033[0m" % zl_nowto20
                    else:
                        zl_nowto20 = "\033[1;34m%s%%\033[0m" % zl_nowto20

                    # 现价与主力成本涨跌幅
                    try:
                        now2zlcb = "%.2f" % ((self.now_format_stock_dict[code]['trade'] - self.now_format_stock_dict[code]['zlcb'] ) / self.now_format_stock_dict[code]['zlcb'] * 100)
                    except:
                        now2zlcb = "0.00"
                    if float(now2zlcb) > 0:
                        now2zlcb = "\033[1;31m%s%%\033[0m" % now2zlcb
                    else:
                        now2zlcb = "\033[1;34m%s%%\033[0m" % now2zlcb

                    # 当前净流入
                    if self.now_format_stock_dict[code]['jlr'] > 1000:
                        now_jlr = "\033[1;31m%.2f\033[0m" % self.now_format_stock_dict[code]['jlr']
                    elif self.now_format_stock_dict[code]['jlr'] >= 500:
                        now_jlr = "\033[1;33m%.2f\033[0m" % self.now_format_stock_dict[code]['jlr']
                    elif self.now_format_stock_dict[code]['jlr'] <= 100:
                        now_jlr = "\033[1;32m%.2f\033[0m" % self.now_format_stock_dict[code]['jlr']
                    else:
                        now_jlr = self.now_format_stock_dict[code]['jlr']

                    # 主力排名
                    if self.now_format_stock_dict[code]['zlrank_today'] < self.now_format_stock_dict[code]['zlrank_5d'] and self.now_format_stock_dict[code]['zlrank_today'] < self.now_format_stock_dict[code]['zlrannk_10d']:
                        zlrank_today = "\033[1;31m%s\033[0m" % self.now_format_stock_dict[code]['zlrank_today']
                    elif self.now_format_stock_dict[code]['zlrank_today'] < self.now_format_stock_dict[code]['zlrank_5d']:
                        zlrank_today = "\033[1;33m%s\033[0m" % self.now_format_stock_dict[code]['zlrank_today']
                    else:
                        zlrank_today = self.now_format_stock_dict[code]['zlrank_today']

                    # 资金动向
                    if self.now_format_stock_dict[code]['zjdx1'] == 1:
                        zjdx1 = "\033[1;31m增仓\033[0m"
                    elif self.now_format_stock_dict[code]['zjdx1'] == 0:
                        zjdx1 = "\033[1;32m减仓\033[0m"
                    else:
                        zjdx1 = "中立"

                    # 行业动向
                    if self.now_format_stock_dict[code]['hydx1'] == 1:
                        hydx1 = "\033[1;31m增仓\033[0m"
                    elif self.now_format_stock_dict[code]['hydx1'] == 0:
                        hydx1 = "\033[1;32m减仓\033[0m"
                    else:
                        hydx1 = "中立"

                    # 今日打败
                    if float(self.now_format_stock_dict[code]['drbx']) >= 95:
                        drbx = "\033[1;31m%.2f\033[0m" % float(self.now_format_stock_dict[code]['drbx'])
                    else:
                        drbx = "%.2f" % float(self.now_format_stock_dict[code]['drbx'])

                    # 上涨概率
                    if float(self.now_format_stock_dict[code]['crsl']) >= 48:
                        crsl = "\033[1;31m%.2f\033[0m" % float(self.now_format_stock_dict[code]['crsl'])
                    else:
                        crsl = "%.2f" % float(self.now_format_stock_dict[code]['crsl'])

                    # 市场关注度
                    if self.now_format_stock_dict[code]['focus'] >= 85:
                        focus = "\033[1;31m%.2f\033[0m" % float(self.now_format_stock_dict[code]['focus'])
                    else:
                        focus = "%.2f" % float(self.now_format_stock_dict[code]['focus'])

                    # 参与意愿
                    if self.now_format_stock_dict[code]['cyyy'] > 0:
                        cyyy = "\033[1;31m%.2f\033[0m" % float(self.now_format_stock_dict[code]['cyyy'])
                    else:
                        cyyy = "%.2f" % float(self.now_format_stock_dict[code]['cyyy'])

                    # 平均盈亏
                    if self.now_format_stock_dict[code]['pjyk'] <= 0:
                        pjyk = "\033[1;31m%.2f\033[0m" % float(self.now_format_stock_dict[code]['pjyk'])
                    else:
                        pjyk = "%.2f" % self.now_format_stock_dict[code]['pjyk']

                    # 60日盈亏
                    if self.now_format_stock_dict[code]['yk60r_zdf'] <= 20:
                        yk60r_zdf = "\033[1;31m%.2f\033[0m" % float(self.now_format_stock_dict[code]['yk60r_zdf'])
                    else:
                        yk60r_zdf = "%.2f" % float(self.now_format_stock_dict[code]['yk60r_zdf'])


                    # 第一行: 市场分析
                    msg = "[%s][%s][市场分析] 得分:%s, 昨日市场排名:%s[%s], 打败 %s 的股票, 今日上涨概率:%s , 市场关注度:%s 参与意愿:%s 平均盈亏:%s 控盘:%s 资金动向:%s 行业动向:%s\n" % (
                                                                                                            code, \
                                                                                                            self.now_format_stock_dict[code]['name'], \
                                                                                                            score, \
                                                                                                            rank, \
                                                                                                            rankup, \
                                                                                                            drbx, \
                                                                                                            crsl, \
                                                                                                            focus, \
                                                                                                            cyyy, \
                                                                                                            pjyk, \
                                                                                                            self.now_format_stock_dict[code]['kpType'], \
                                                                                                            zjdx1, \
                                                                                                            hydx1

                    )

                    # 第二行: 涨跌状况
                    msg += "[%s][%s][涨跌状况] 价格(现/主/市)[%s/%.2f/%.2f] 近期涨跌幅(今/昨/5/10/60):%s/%s/%s/%s/%s 近期净流入(今|昨|5日):%s|%s|%s万\n" % (
                                                                                                            code, \
                                                                                                            self.now_format_stock_dict[code]['name'], \
                                                                                                            now_trade, \
                                                                                                            float(self.now_format_stock_dict[code]['zlcb']), \
                                                                                                            float(self.now_format_stock_dict[code]['sccb']), \
                                                                                                            now_zdf, \
                                                                                                            self.now_format_stock_dict[code]['yestoday_zdf'], \
                                                                                                            float(self.now_format_stock_dict[code]['zdf_5d']), \
                                                                                                            self.now_format_stock_dict[code]['zdf_10d'], \
                                                                                                            yk60r_zdf, \
                                                                                                            now_jlr, \
                                                                                                            self.now_format_stock_dict[code]['yestoday_jlr'], \
                                                                                                            self.now_format_stock_dict[code]['jlr_5days']
                    )
                    # kdj
                    kdj_num = [ float(s['kdj'][0])>float(s['kdj'][1])  for s in self.stock_jx_data[code]].count(True)
                    _kdj_avg = [ "%.2f" % (sum([float(ii) for ii in s['kdj']])/3) for s in self.stock_jx_data[code]]
                    kdj_avg = "%s->%s->%s->%s->%s" % (_kdj_avg[0], _kdj_avg[1], _kdj_avg[2], _kdj_avg[3], _kdj_avg[4])
                    k = "%.2f" % float(self.stock_jx_data[code][-1]['kdj'][0])
                    d = "%.2f" % float(self.stock_jx_data[code][-1]['kdj'][1])
                    j = "%.2f" % float(self.stock_jx_data[code][-1]['kdj'][2])
                    # macd
                    _macd_avg = [ "%.2f" % float(i['macd'][0]) for i in self.stock_jx_data[code]]
                    macd_avg = "%s->%s->%s->%s->%s" % (_macd_avg[0], _macd_avg[1], _macd_avg[2], _macd_avg[3], _macd_avg[4])
                    macd =  "%.2f" % float(self.stock_jx_data[code][-1]['macd'][0])
                    macd_diff = "%.2f" % float(self.stock_jx_data[code][-1]['macd'][1])
                    macd_dea = "%.2f" % float(self.stock_jx_data[code][-1]['macd'][2])

                    # kdj_info
                    kdj_info = "kdj_num:%s last_kdj:[%s, %s, %s] 近5平均kdj:%s" % (kdj_num, k, d, j, kdj_avg)
                    if float(_kdj_avg[4]) > float(_kdj_avg[3]) and float(_kdj_avg[3]) > float(_kdj_avg[2]) and float(_kdj_avg[4]) > float(_kdj_avg[2]):
                        # red
                        kdj_info = "\033[1;31m%s\033[0m" % kdj_info

                    # macd_info
                    macd_info = "last_macd:[%s, %s, %s] 近5平均macd:%s" % (macd, macd_diff, macd_dea, macd_avg)
                    if float(_macd_avg[4]) > float(_macd_avg[3]) and float(_macd_avg[3]) > float(_macd_avg[2]) and float(_macd_avg[4]) > float(_macd_avg[2]) \
                    and True in [float(i['macd'][0]) < 0 for i in self.stock_jx_data[code]]:
                        # red
                        macd_info = "\033[1;31m%s\033[0m" % macd_info

                    fh = "\033[1;37m+\033[0m"
                    msg += "[%s][%s][技术面] %s | %s\n" % (code, self.now_format_stock_dict[code]['name'], kdj_info, macd_info)
                    msg += "[%s][%s][基本面] %s\n" % (code, self.now_format_stock_dict[code]['name'], self.now_format_stock_dict[code]['value_summary'])
                    msg += "[%s][%s][资金面] %s" % (code, self.now_format_stock_dict[code]['name'], self.now_format_stock_dict[code]['summary'])

                    print msg
                    print "-"*150
                except Exception as e:
                    print("[-][%s] not found",e)
                    print "-"*150

    def work(self, code):
        if code:
            # > ----------------------------- * 获取所有股票代码均线数据 * --------------------------
            self.get_all_code()
            self.get_anaylse_data()

            # > ----------------------------- * 获取均线数据方法 * --------------------------
            self.get_jx_data()

            # > ----------------------------- * 获取当日实时数据方法 * --------------------------
            ntime = "\033[1;32m%s\033[0m" % str(time.strftime('%H:%M:%S' , time.localtime()))
            fh = "\033[1;37m+\033[0m"
            print("[%s][%s] 获取实时股票数据中, 请稍后..." % (fh, ntime))
            print("_"*150)
            self.format_realtime_data()

            self.print_stock_info(code)

        else:
            # > ----------------------------- 1. 监听进度线程 ----------------------------- 
            threading.Thread(target=self.status_monitor, args=()).start()

            t1 = threading.Thread(target=self.monitor_money_flow, args=())
            t1.start()

            # > ----------------------------- * 获取股票代码列表&前缀 * -----------------------------
            # > ----------------------------- * 获取所有股票代码均线数据 * --------------------------
            self.get_all_code()

            # 获取T+1日东方财富数据结果并缓存
            self.get_anaylse_data()
            self.anaylse_data_status = 2
            ntime = "\033[1;32m%s\033[0m" % str(time.strftime('%H:%M:%S' , time.localtime()))
            n_count = "\033[1;31m%s\033[0m" % len(self.stock_anaylse_dict.keys())
            fh = "\033[1;31m*\033[0m"
            print("[%s][%s] 分析数据收集完毕, 共分析出 %s 条股票信息." % (fh, ntime, n_count))

            # > ----------------------------- * 获取均线数据方法 * --------------------------
            self.get_jx_data()
            ntime = "\033[1;32m%s\033[0m" % str(time.strftime('%H:%M:%S' , time.localtime()))
            n_count = "\033[1;31m%s\033[0m" % len(self.stock_jx_data.keys())
            fh = "\033[1;31m*\033[0m"
            print("[%s][%s] 均线数据收集完毕, 共收集出 %s 条股票信息." % (fh, ntime, n_count))

            # 获取均线数据结束
            self.jx_data_status = 2

            while True:
                if int(time.strftime('%H' , time.localtime())) >= 11 and int(time.strftime('%H' , time.localtime())) < 13:
                    if int(time.strftime('%H' , time.localtime())) == 11:
                        if int(time.strftime('%M' , time.localtime())) >= 30:
                            print("[-] 午市休息中..")
                            time.sleep(5)
                            continue
                    elif int(time.strftime('%H' , time.localtime())) == 12 and int(time.strftime('%M' , time.localtime())) >= 55:
                        pass
                    else:
                        print("[-] 午市休息中..")
                        time.sleep(5)
                        continue

                # > ----------------------------- * 获取昨日收盘数据方法 * --------------------------
                with gevent.Timeout(300, False) as timeout:
                    self.get_yestody_stock()
                    fh = "\033[1;31m*\033[0m"
                    ntime = "\033[1;32m%s\033[0m" % str(time.strftime('%H:%M:%S' , time.localtime()))
                    n_count = "\033[1;37m%s\033[0m" % str(len(self.yestoday_stock_dict.keys()))
                    print("[%s][%s] 昨日数据收集完毕, 共收集出 %s 条股票信息." % (fh, ntime, n_count))

                # 获取昨日数据结束
                self.ys_data_status = 2

                self.jx_data_count = 0
                self.ys_data_count = 0
                self.now_data_count = 0

                # > ----------------------------- * 获取当日收盘数据方法 * --------------------------
                with gevent.Timeout(300, False) as timeout:
                    self.get_now_stock()
                    ntime = "\033[1;32m%s\033[0m" % str(time.strftime('%H:%M:%S' , time.localtime()))
                    fh = "\033[1;31m*\033[0m"
                    n_count = "\033[1;37m%s\033[0m" % str(len(self.now_stock_dict.keys()))
                    print("[%s][%s] 今日数据收集完毕, 共收集出 %s 条股票信息." % (fh, ntime, n_count))

                # 将当前自选加入监控列表
                if self.is_zxg_monitor:
                    if self.first_zxg_add:
                        for code in self.zxg_list:
                            sn.rule_matched_list['rule1'].append(code)

                            self.first_zxg_add = False

                        # 执行一次
                        self.monitor_money_flow(once=True)

                # 获取当日数据结束
                self.now_data_status = 2

                # > ----------------------------- * 过滤规则 * --------------------------
                self.rule_filter()

                fh = "\033[1;31m*\033[0m"
                ntime = "\033[1;32m%s\033[0m" % str(time.strftime('%H:%M:%S' , time.localtime()))
                n_count = "\033[1;31m%s\033[0m" % str((len(self.yestoday_stock_dict.keys()) - len(self.now_stock_dict.keys())))
                print "[%s][%s] 本次分析完毕, 昨日数据 与 今日数据比相差 %s 个." % (fh, ntime, n_count)

                if int(time.strftime('%H' , time.localtime())) >= 15:
                    msg = "\033[1;37m[-][%s] 交易已结束, 退出..\033[0m" % ntime
                    print(msg)
                    self.close_signal = True
                    sys.exit()


    def market_analyse(self):
        """
        分析指标详情
        1. 当前行情情况,
          - 大跌行情(1.5倍的都在跌)
          - 大涨行情(1.5倍的都在涨)
          - 中等偏上行情(小于1.5，大于1.9的在涨)
          - 中等偏下行情(大于1.5)
        """


        # 数据补齐
        # 1. macd
        for i in self.now_format_stock_dict:
            if 'jx_data' not in self.now_format_stock_dict[i].keys():
                 self.now_format_stock_dict[i]['jx_data'] = [{'code':i, 'macd':[0,0,0], 'kdj':[0,0,0]}]


        # 2. zdf
        for i in self.now_format_stock_dict:
            if 'zdf' not in self.now_format_stock_dict[i].keys():
                self.now_format_stock_dict[i]['zdf'] = 0

        # 3. jlr
        for i in self.now_format_stock_dict:
            if 'jlr' not in self.now_format_stock_dict[i].keys():
                 self.now_format_stock_dict[i]['jlr'] = 0

        # 4.ma5
        for code in self.stock_jx_data:
            if code in self.now_format_stock_dict.keys():
                self.now_format_stock_dict[code]['ma5'] = self.stock_jx_data[code][-1]['ma5']

        for i in self.now_format_stock_dict:
            if 'ma5' not in self.now_format_stock_dict[i].keys():
                 self.now_format_stock_dict[i]['ma5'] = 0

        # 5.trade
        for i in self.now_format_stock_dict:
            if 'trade' not in self.now_format_stock_dict[i].keys():
                 self.now_format_stock_dict[i]['trade'] = 0


        # -------------------------------------- 今日
        #当前总数
        total_stock_num = len([ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if 'zdf' in self.now_format_stock_dict[i].keys()])

        #当前跌数
        down_stock_num = len([ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if 'zdf' in self.now_format_stock_dict[i].keys() and float(self.now_format_stock_dict[i]['zdf']) < 0])

        #当前涨数
        up_stock_num = len([ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if 'zdf' in self.now_format_stock_dict[i].keys() and float(self.now_format_stock_dict[i]['zdf']) > 0])

        # 大跌
        if down_stock_num > total_stock_num / 1.6:
            jr_hq = "\033[1;32m大跌行情\033[0m"
        # 大涨
        elif up_stock_num > total_stock_num / 1.6:
            jr_hq = "\033[1;31m大涨行情\033[0m"
        # 中等偏上
        elif down_stock_num < up_stock_num:
            jr_hq = "\033[1;33m中等偏上行情\033[0m"
        # 中等偏下
        elif down_stock_num > up_stock_num:
            jr_hq = "\033[1;36m中等偏下行情\033[0m"
        else:
            jr_hq = "\033[1;37m其他情况\033[0m"

        # -------------------------------------- 昨日
        #昨日总数
        total_stock_num = len([ self.now_format_stock_dict[i]['yestoday_zdf'] for i in self.now_format_stock_dict if 'yestoday_zdf' in self.now_format_stock_dict[i].keys()])

        #昨日跌数
        down_stock_num = len([ self.now_format_stock_dict[i]['yestoday_zdf'] for i in self.now_format_stock_dict if 'yestoday_zdf' in self.now_format_stock_dict[i].keys() and float(self.now_format_stock_dict[i]['yestoday_zdf']) < 0])

        #昨日涨数
        up_stock_num = len([ self.now_format_stock_dict[i]['yestoday_zdf'] for i in self.now_format_stock_dict if 'yestoday_zdf' in self.now_format_stock_dict[i].keys() and float(self.now_format_stock_dict[i]['yestoday_zdf']) > 0])

        # 大跌
        if down_stock_num > total_stock_num / 1.6:
            zr_hq = "\033[1;32m大跌行情\033[0m"
        # 大涨
        elif up_stock_num > total_stock_num / 1.6:
            zr_hq = "\033[1;31m大涨行情\033[0m"
        # 中等偏上
        elif down_stock_num < up_stock_num:
            zr_hq = "\033[1;33m中等偏上行情\033[0m"
        # 中等偏下
        elif down_stock_num > up_stock_num:
            zr_hq = "\033[1;36m中等偏下行情\033[0m"
        else:
            zr_hq = "\033[1;37m其他情况\033[0m"

        print "[*] 昨日: %s | 当前: %s" % (zr_hq, jr_hq)
        print "-"*150

        """
          - 涨停数
          - 跌停数
        """
        rule0_up_num = [ code for code in self.now_format_stock_dict if (code.startswith('300') and self.now_format_stock_dict[code]['zdf'] >= 19.9) or (code.startswith('300') is False and self.now_format_stock_dict[code]['zdf'] >= 9.9)].__len__()
        rule0_down_num = [ code for code in self.now_format_stock_dict if (code.startswith('300') and self.now_format_stock_dict[code]['zdf'] <= -19.9) or (code.startswith('300') is False and self.now_format_stock_dict[code]['zdf'] <= -9.9)].__len__()
        rule0_up_num = "\033[1;31m%s\033[0m" % rule0_up_num
        rule0_down_num = "\033[1;32m%s\033[0m" % rule0_down_num
        rule0_up = [ self.now_format_stock_dict[code]['zdf']>0 for code in self.now_format_stock_dict ].count(True)
        rule0_down = [ self.now_format_stock_dict[code]['zdf']>0 for code in self.now_format_stock_dict ].count(False)
        rule0_up = "\033[1;31m%s\033[0m" % rule0_up
        rule0_down = "\033[1;32m%s\033[0m" % rule0_down

        rule0_up_list = 
        rule0_down_list = 

        print('[!] 当前上涨 %s 支, 下跌 %s支; 其中涨停 %s 支, 跌停 %s 支' % (rule0_up, rule0_down, rule0_up_num, rule0_down_num))
        print "-"*150
        """
          - 涨幅大于0 < 2的股票
          - 涨幅大于2 < 5的股票
          - 涨幅大于5的股票

          - 涨幅小于<0 > -2的股票
          - 涨幅小于<0 > -5的股票
          - 涨幅小于-5的股票

        """
        rule15_num = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['zdf'] > 0 and self.now_format_stock_dict[code]['zdf'] < 2 ].__len__()
        print('[+] 命中 "\033[1;31m涨幅大于0小于2\033[0m" 的股票 %s 支' % rule15_num)
        rule16_num = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['zdf'] > 2 and self.now_format_stock_dict[code]['zdf'] < 5 ].__len__()
        print('[+] 命中 "\033[1;31m涨幅大于2小于5\033[0m" 的股票 %s 支' % rule16_num)
        rule17_num = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['zdf'] > 5 and self.now_format_stock_dict[code]['zdf'] < 1000 ].__len__()
        print('[+] 命中 "\033[1;31m涨幅大于5\033[0m" 的股票 %s 支' % rule17_num)
        rule18_num = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['zdf'] < 0 and self.now_format_stock_dict[code]['zdf'] > -2 ].__len__()
        print('[+] 命中 "\033[1;31m涨幅小于0大于-2\033[0m" 的股票 %s 支' % rule18_num)
        rule19_num = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['zdf'] < -2 and self.now_format_stock_dict[code]['zdf'] > -5 ].__len__()
        print('[+] 命中 "\033[1;31m涨幅小于-2大于-5的股票\033[0m" 的股票 %s 支' % rule19_num)
        rule20_num = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['zdf'] < -5 ].__len__()
        print('[+] 命中 "\033[1;31m涨幅小于-5的股票\033[0m" 的股票 %s 支' % rule20_num)
        print '-'*150

        """
          - 昨日macd为正的涨跌情况, 以及平均涨跌幅
            - 多少上涨
            - 多少下跌
        """
        rule1_num = [ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][0])>0].__len__()
        rule1_up = [ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][0])>0 and self.now_format_stock_dict[i]['zdf']>0].__len__()
        rule1_up_sum = sum([ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][0])>0 and self.now_format_stock_dict[i]['zdf']>0])
        rule1_up_avg = rule1_up_sum/rule1_up
        rule1_down = [ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][0])>0 and self.now_format_stock_dict[i]['zdf']<0].__len__()
        rule1_down_sum = sum([ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][0])>0 and self.now_format_stock_dict[i]['zdf']<0])
        rule1_down_avg = rule1_down_sum/rule1_down
        # print('[+] 命中 "\033[1;31m@@@@@\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (,))
        print('[+] 命中 "\033[1;31m昨日macd为正\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule1_num,rule1_up,rule1_up_avg,rule1_down,rule1_down_avg))

        """
          - 昨日macd为正且diff>-0.01的涨跌情况
            - 多少上涨
            - 多少下跌

        """
        rule2_num = [ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][0])>0 and float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][1]) > -0.01].__len__()
        rule2_up = [ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][0])>0 and float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][1]) > -0.01 and self.now_format_stock_dict[i]['zdf']>0].__len__()
        rule2_up_sum = sum([ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][0])>0 and float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][1]) > -0.01 and self.now_format_stock_dict[i]['zdf']>0])
        rule2_up_avg = rule2_up_sum/rule2_up
        rule2_down = [ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][0])>0 and float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][1]) > -0.01 and self.now_format_stock_dict[i]['zdf']<0].__len__()
        rule2_down_sum = sum([ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][0])>0 and float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][1]) > -0.01 and self.now_format_stock_dict[i]['zdf']<0])
        rule2_down_avg = rule2_down_sum/rule2_down
        print('[+] 命中 "\033[1;31m昨日macd为正且diff>-0.01\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule2_num,rule2_up,rule2_up_avg,rule2_down,rule2_down_avg))


        """

          - 昨日macd为正且diff<-0.01的涨跌情况
            - 多少上涨
            - 多少下跌
        """
        rule3_num = [ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][0])>0 and float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][1]) < -0.01].__len__()
        rule3_up = [ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][0])>0 and float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][1]) < -0.01 and self.now_format_stock_dict[i]['zdf']>0].__len__()
        rule3_up_sum = sum([ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][0])>0 and float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][1]) < -0.01 and self.now_format_stock_dict[i]['zdf']>0])
        rule3_up_avg = rule3_up_sum/rule3_up
        rule3_down = [ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][0])>0 and float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][1]) < -0.01 and self.now_format_stock_dict[i]['zdf']<0].__len__()
        rule3_down_sum = sum([ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][0])>0 and float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][1]) < -0.01 and self.now_format_stock_dict[i]['zdf']<0])
        rule3_down_avg = rule3_down_sum/rule3_down
        print('[+] 命中 "\033[1;31m昨日macd为正且diff<-0.01\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule3_num,rule3_up,rule3_up_avg,rule3_down,rule3_down_avg))


        """
          - 昨日macd为负的涨跌情况
            - 多少上涨
            - 多少下跌

          - 昨日macd为负且diff>-0.01的涨跌情况
            - 多少上涨
            - 多少下跌

          - 昨日macd为负且diff<-0.01的涨跌情况
            - 多少上涨
            - 多少下跌
        """
        rule4_num = [ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][0])>0].__len__()
        rule4_up = [ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][0])>0 and self.now_format_stock_dict[i]['zdf']>0].__len__()
        rule4_up_sum = sum([ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][0])>0 and self.now_format_stock_dict[i]['zdf']>0])
        rule4_up_avg = rule4_up_sum/rule4_up
        rule4_down = [ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][0])>0 and self.now_format_stock_dict[i]['zdf']<0].__len__()
        rule4_down_sum = sum([ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][0])>0 and self.now_format_stock_dict[i]['zdf']<0])
        rule4_down_avg = rule4_down_sum/rule4_down
        print('[+] 命中 "\033[1;31m昨日macd为负\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule4_num,rule4_up,rule4_up_avg,rule4_down,rule4_down_avg))

        rule5_num = [ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][0])>0 and float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][1]) > -0.01].__len__()
        rule5_up = [ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][0])>0 and float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][1]) > -0.01 and self.now_format_stock_dict[i]['zdf']>0].__len__()
        rule5_up_sum = sum([ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][0])>0 and float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][1]) > -0.01 and self.now_format_stock_dict[i]['zdf']>0])
        rule5_up_avg = rule5_up_sum/rule5_up
        rule5_down = [ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][0])>0 and float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][1]) > -0.01 and self.now_format_stock_dict[i]['zdf']<0].__len__()
        rule5_down_sum = sum([ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][0])>0 and float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][1]) > -0.01 and self.now_format_stock_dict[i]['zdf']<0])
        rule5_down_avg = rule5_down_sum/rule5_down
        print('[+] 命中 "\033[1;31m昨日macd为负且diff>-0.01\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule5_num,rule5_up,rule5_up_avg,rule5_down,rule5_down_avg))

        rule6_num = [ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][0])>0 and float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][1]) < -0.01].__len__()
        rule6_up = [ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][0])>0 and float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][1]) < -0.01 and self.now_format_stock_dict[i]['zdf']>0].__len__()
        rule6_up_sum = sum([ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][0])>0 and float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][1]) < -0.01 and self.now_format_stock_dict[i]['zdf']>0])
        rule6_up_avg = rule6_up_sum/rule6_up
        rule6_down = [ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][0])>0 and float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][1]) < -0.01 and self.now_format_stock_dict[i]['zdf']<0].__len__()
        rule6_down_sum = sum([ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][0])>0 and float(self.now_format_stock_dict[i]['jx_data'][-1]['macd'][1]) < -0.01 and self.now_format_stock_dict[i]['zdf']<0])
        rule6_down_avg = rule6_down_sum/rule6_down
        print('[+] 命中 "\033[1;31m昨日macd为负且diff<-0.01\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule6_num,rule6_up,rule6_up_avg,rule6_down,rule6_down_avg))

        """
          - 昨日kdj平均值>0<30涨跌
            - 多少上涨
            - 多少下跌

          - 昨日kdj平均值>30<50涨跌

          - 昨日kdj平均值>50<80涨跌

          - 昨日kdj平均值>80<100涨跌
        """
        rule7_num = [ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3>0 and sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3<30].__len__() 
        rule7_up = [ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3>0 and sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3<30 and self.now_format_stock_dict[i]['zdf']>0].__len__() 
        rule7_up_sum = sum([ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3>0 and sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3<30 and self.now_format_stock_dict[i]['zdf']>0])
        rule7_up_avg = rule7_up_sum/rule7_up
        rule7_down = [ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3>0 and sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3<30 and self.now_format_stock_dict[i]['zdf']<0].__len__() 
        rule7_down_sum = sum([ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3>0 and sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3<30 and self.now_format_stock_dict[i]['zdf']<0])
        rule7_down_avg = rule7_down_sum/rule7_down
        print('[+] 命中 "\033[1;31m昨日kdj平均值>0<30\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule7_num,rule7_up,rule7_up_avg,rule7_down,rule7_down_avg))

        rule8_num = [ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3>30 and sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3<50].__len__()
        rule8_up = [ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3>30 and sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3<50 and self.now_format_stock_dict[i]['zdf']>0].__len__()
        rule8_up_sum = sum([ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3>30 and sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3<50 and self.now_format_stock_dict[i]['zdf']>0])
        rule8_up_avg = rule8_up_sum/rule8_up
        rule8_down = [ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3>30 and sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3<50 and self.now_format_stock_dict[i]['zdf']<0].__len__()
        rule8_down_sum = sum([ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3>30 and sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3<50 and self.now_format_stock_dict[i]['zdf']<0])
        rule8_down_avg = rule8_down_sum/rule8_down
        print('[+] 命中 "\033[1;31m昨日kdj平均值>30<50\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule8_num,rule8_up,rule8_up_avg,rule8_down,rule8_down_avg))

        rule9_num = [ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3>50 and sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3<80].__len__() 
        rule9_up = [ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3>50 and sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3<80 and self.now_format_stock_dict[i]['zdf']>0].__len__() 
        rule9_up_sum = sum([ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3>50 and sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3<80 and self.now_format_stock_dict[i]['zdf']>0])
        rule9_up_avg = rule9_up_sum/rule9_up
        rule9_down = [ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3>50 and sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3<80 and self.now_format_stock_dict[i]['zdf']<0].__len__() 
        rule9_down_sum = sum([ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3>50 and sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3<80 and self.now_format_stock_dict[i]['zdf']<0])
        rule9_down_avg = rule9_down_sum/rule9_down
        print('[+] 命中 "\033[1;31m昨日kdj平均值>50<80\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule9_num,rule9_up,rule9_up_avg,rule9_down,rule9_down_avg))

        rule10_num = [ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3>80 ].__len__() 
        rule10_up = [ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3>80 and self.now_format_stock_dict[i]['zdf']>0 ].__len__() 
        rule10_up_sum = sum([ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3>80 and self.now_format_stock_dict[i]['zdf']>0 ])
        rule10_up_avg = rule10_up_sum/rule10_up
        rule10_down = [ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3>80 and self.now_format_stock_dict[i]['zdf']<0 ].__len__() 
        rule10_down_sum = sum([ self.now_format_stock_dict[i]['zdf'] for i in self.now_format_stock_dict if sum([ float(j) for j in self.now_format_stock_dict[i]['jx_data'][-1]['kdj'] ])/3>80 and self.now_format_stock_dict[i]['zdf']<0 ])
        rule10_down_avg = rule10_down_sum/rule10_down
        print('[+] 命中 "\033[1;31m昨日kdj平均值>80<100\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule10_num,rule10_up,rule10_up_avg,rule10_down,rule10_down_avg))


        """
          - 市值大于150<300亿涨跌情况
          - 市值大于300亿小于1000亿涨跌情况
          - 市值大于1000亿的涨跌情况
          - 市值小于150亿涨跌情况
        """

        rule11_num = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['volume'] > 150 and self.now_format_stock_dict[code]['volume'] < 300].__len__()
        rule11_up = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['volume'] > 150 and self.now_format_stock_dict[code]['volume'] < 300 and self.now_format_stock_dict[code]['zdf']>0].__len__()
        rule11_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['volume'] > 150 and self.now_format_stock_dict[code]['volume'] < 300 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule11_up_avg = rule11_up_sum/rule11_up
        rule11_down = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['volume'] > 150 and self.now_format_stock_dict[code]['volume'] < 300 and self.now_format_stock_dict[code]['zdf']<0].__len__()
        rule11_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['volume'] > 150 and self.now_format_stock_dict[code]['volume'] < 300 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule11_down_avg = rule11_down_sum/rule11_down
        print('[+] 命中 "\033[1;31m市值大于150<300亿\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule11_num,rule11_up,rule11_up_avg,rule11_down,rule11_down_avg))

        rule12_num = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['volume'] > 300 and self.now_format_stock_dict[code]['volume'] < 1000].__len__()
        rule12_up = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['volume'] > 300 and self.now_format_stock_dict[code]['volume'] < 1000 and self.now_format_stock_dict[code]['zdf']>0].__len__()
        rule12_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['volume'] > 300 and self.now_format_stock_dict[code]['volume'] < 1000 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule12_up_avg = rule12_up_sum/rule12_up
        rule12_down = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['volume'] > 300 and self.now_format_stock_dict[code]['volume'] < 1000 and self.now_format_stock_dict[code]['zdf']<0].__len__()
        rule12_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['volume'] > 300 and self.now_format_stock_dict[code]['volume'] < 1000 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule12_down_avg = rule12_down_sum/rule12_down
        print('[+] 命中 "\033[1;31m市值大于300亿小于1000亿\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule12_num,rule12_up,rule12_up_avg,rule12_down,rule12_down_avg))

        rule13_num = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['volume'] > 1000 and self.now_format_stock_dict[code]['volume'] < 100000].__len__()
        rule13_up = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['volume'] > 1000 and self.now_format_stock_dict[code]['volume'] < 100000 and self.now_format_stock_dict[code]['zdf']>0].__len__()
        rule13_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['volume'] > 1000 and self.now_format_stock_dict[code]['volume'] < 100000 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule13_up_avg = rule13_up_sum/rule13_up
        rule13_down = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['volume'] > 1000 and self.now_format_stock_dict[code]['volume'] < 100000 and self.now_format_stock_dict[code]['zdf']<0].__len__()
        rule13_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['volume'] > 1000 and self.now_format_stock_dict[code]['volume'] < 100000 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule13_down_avg = rule13_down_sum/rule13_down
        print('[+] 命中 "\033[1;31m市值大于1000亿\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule13_num,rule13_up,rule13_up_avg,rule13_down,rule13_down_avg))

        rule14_num = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['volume'] < 150].__len__()
        rule14_up = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['volume'] < 150 and self.now_format_stock_dict[code]['zdf']>0].__len__()
        rule14_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['volume'] < 150 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule14_up_avg = rule14_up_sum/rule14_up
        rule14_down = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['volume'] < 150 and self.now_format_stock_dict[code]['zdf']<0].__len__()
        rule14_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['volume'] < 150 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule14_down_avg = rule14_down_sum/rule14_down
        print('[+] 命中 "\033[1;31m市值小于150亿\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule14_num,rule14_up,rule14_up_avg,rule14_down,rule14_down_avg))

        """
          - 60日涨幅>0%<10涨跌情况
          - 60日涨幅>10%<30涨跌情况
          - 60日涨幅>30%<50涨跌情况
          - 60日涨幅>50%涨跌情况

          - 60日涨幅<0, >-10%涨跌情况
          - 60日涨幅<-10, >-30%涨跌情况
          - 60日涨幅<-30, >-50%涨跌情况

          - 5日涨幅>0<10%涨跌情况
          - 5日涨幅>10%<20涨跌情况
          - 5日涨幅>20%涨跌情况

          - 5日涨幅小于0，大于-10的涨跌情况
          - 5日涨幅小于-10，大于-20的涨跌情况

        """
        rule21_num = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] > 0 and self.now_format_stock_dict[code]['yk60r_zdf'] < 10 ].__len__()
        rule21_up = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] > 0 and self.now_format_stock_dict[code]['yk60r_zdf'] < 10 and float(self.now_format_stock_dict[code]['zdf'])>0].__len__()
        rule21_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] > 0 and self.now_format_stock_dict[code]['yk60r_zdf'] < 10 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule21_up_avg = rule21_up_sum/rule21_up
        rule21_down = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] > 0 and self.now_format_stock_dict[code]['yk60r_zdf'] < 10 and float(self.now_format_stock_dict[code]['zdf'])<0].__len__()
        rule21_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] > 0 and self.now_format_stock_dict[code]['yk60r_zdf'] < 10 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule21_down_avg = rule21_down_sum/rule21_down

        rule22_num = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] > 10 and self.now_format_stock_dict[code]['yk60r_zdf'] < 30 ].__len__()
        rule22_up = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] > 10 and self.now_format_stock_dict[code]['yk60r_zdf'] < 30 and float(self.now_format_stock_dict[code]['zdf'])>0].__len__()
        rule22_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] > 10 and self.now_format_stock_dict[code]['yk60r_zdf'] < 30 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule22_up_avg = rule22_up_sum/rule22_up
        rule22_down = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] > 10 and self.now_format_stock_dict[code]['yk60r_zdf'] < 30 and float(self.now_format_stock_dict[code]['zdf'])<0].__len__()
        rule22_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] > 10 and self.now_format_stock_dict[code]['yk60r_zdf'] < 30 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule22_down_avg = rule22_down_sum/rule22_down

        rule23_num = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] > 30 and self.now_format_stock_dict[code]['yk60r_zdf'] < 50 ].__len__()
        rule23_up = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] > 30 and self.now_format_stock_dict[code]['yk60r_zdf'] < 50 and float(self.now_format_stock_dict[code]['zdf'])>0].__len__()
        rule23_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] > 30 and self.now_format_stock_dict[code]['yk60r_zdf'] < 50 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule23_up_avg = rule23_up_sum/rule23_up
        rule23_down = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] > 30 and self.now_format_stock_dict[code]['yk60r_zdf'] < 50 and float(self.now_format_stock_dict[code]['zdf'])<0].__len__()
        rule23_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] > 30 and self.now_format_stock_dict[code]['yk60r_zdf'] < 50 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule23_down_avg = rule23_down_sum/rule23_down

        rule24_num = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] > 50 and self.now_format_stock_dict[code]['yk60r_zdf'] < 10000 ].__len__()
        rule24_up = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] > 50 and self.now_format_stock_dict[code]['yk60r_zdf'] < 10000 and float(self.now_format_stock_dict[code]['zdf'])>0].__len__()
        rule24_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] > 50 and self.now_format_stock_dict[code]['yk60r_zdf'] < 10000 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule24_up_avg = rule24_up_sum/rule24_up
        rule24_down = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] > 50 and self.now_format_stock_dict[code]['yk60r_zdf'] < 10000 and float(self.now_format_stock_dict[code]['zdf'])<0].__len__()
        rule24_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] > 50 and self.now_format_stock_dict[code]['yk60r_zdf'] < 10000 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule24_down_avg = rule24_down_sum/rule24_down

        rule25_num = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] < 0 and self.now_format_stock_dict[code]['yk60r_zdf'] > -10 ].__len__()
        rule25_up = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] < 0 and self.now_format_stock_dict[code]['yk60r_zdf'] > -10 and float(self.now_format_stock_dict[code]['zdf'])>0].__len__()
        rule25_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] < 0 and self.now_format_stock_dict[code]['yk60r_zdf'] > -10 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule25_up_avg = rule25_up_sum/rule25_up
        rule25_down = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] < 0 and self.now_format_stock_dict[code]['yk60r_zdf'] > -10 and float(self.now_format_stock_dict[code]['zdf'])<0].__len__()
        rule25_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] < 0 and self.now_format_stock_dict[code]['yk60r_zdf'] > -10 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule25_down_avg = rule25_down_sum/rule25_down

        rule26_num = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] < -10 and self.now_format_stock_dict[code]['yk60r_zdf'] > -30 ].__len__()
        rule26_up = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] < -10 and self.now_format_stock_dict[code]['yk60r_zdf'] > -30 and float(self.now_format_stock_dict[code]['zdf'])>0].__len__()
        rule26_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] < -10 and self.now_format_stock_dict[code]['yk60r_zdf'] > -30 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule26_up_avg = rule26_up_sum/rule26_up
        rule26_down = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] < -10 and self.now_format_stock_dict[code]['yk60r_zdf'] > -30 and float(self.now_format_stock_dict[code]['zdf'])<0].__len__()
        rule26_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] < -10 and self.now_format_stock_dict[code]['yk60r_zdf'] > -30 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule26_down_avg = rule26_down_sum/rule26_down

        rule27_num = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] < -30 and self.now_format_stock_dict[code]['yk60r_zdf'] > -50 ].__len__()
        rule27_up = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] < -30 and self.now_format_stock_dict[code]['yk60r_zdf'] > -50 and float(self.now_format_stock_dict[code]['zdf'])>0].__len__()
        rule27_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] < -30 and self.now_format_stock_dict[code]['yk60r_zdf'] > -50 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule27_up_avg = rule27_up_sum/rule27_up
        rule27_down = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] < -30 and self.now_format_stock_dict[code]['yk60r_zdf'] > -50 and float(self.now_format_stock_dict[code]['zdf'])<0].__len__()
        rule27_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['yk60r_zdf'] < -30 and self.now_format_stock_dict[code]['yk60r_zdf'] > -50 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule27_down_avg = rule27_down_sum/rule27_down

        rule28_num = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['zdf_5d'] > 0 and self.now_format_stock_dict[code]['zdf_5d'] < 10 ].__len__()
        rule28_up = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['zdf_5d'] > 0 and self.now_format_stock_dict[code]['zdf_5d'] < 10 and float(self.now_format_stock_dict[code]['zdf'])>0].__len__()
        rule28_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['zdf_5d'] > 0 and self.now_format_stock_dict[code]['zdf_5d'] < 10 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule28_up_avg = rule28_up_sum/rule28_up
        rule28_down = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['zdf_5d'] > 0 and self.now_format_stock_dict[code]['zdf_5d'] < 10 and float(self.now_format_stock_dict[code]['zdf'])<0].__len__()
        rule28_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['zdf_5d'] > 0 and self.now_format_stock_dict[code]['zdf_5d'] < 10 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule28_down_avg = rule28_down_sum/rule28_down

        rule29_num = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['zdf_5d'] > 10 and self.now_format_stock_dict[code]['zdf_5d'] < 20 ].__len__()
        rule29_up = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['zdf_5d'] > 10 and self.now_format_stock_dict[code]['zdf_5d'] < 20 and float(self.now_format_stock_dict[code]['zdf'])>0].__len__()
        rule29_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['zdf_5d'] > 10 and self.now_format_stock_dict[code]['zdf_5d'] < 20 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule29_up_avg = rule29_up_sum/rule29_up
        rule29_down = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['zdf_5d'] > 10 and self.now_format_stock_dict[code]['zdf_5d'] < 20 and float(self.now_format_stock_dict[code]['zdf'])<0].__len__()
        rule29_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['zdf_5d'] > 10 and self.now_format_stock_dict[code]['zdf_5d'] < 20 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule29_down_avg = rule29_down_sum/rule29_down

        rule30_num = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['zdf_5d'] > 20 and self.now_format_stock_dict[code]['zdf_5d'] < 10000 ].__len__()
        rule30_up = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['zdf_5d'] > 20 and self.now_format_stock_dict[code]['zdf_5d'] < 10000 and float(self.now_format_stock_dict[code]['zdf'])>0].__len__()
        rule30_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['zdf_5d'] > 20 and self.now_format_stock_dict[code]['zdf_5d'] < 10000 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule30_up_avg = rule30_up_sum/rule30_up
        rule30_down = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['zdf_5d'] > 20 and self.now_format_stock_dict[code]['zdf_5d'] < 10000 and float(self.now_format_stock_dict[code]['zdf'])<0].__len__()
        rule30_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['zdf_5d'] > 20 and self.now_format_stock_dict[code]['zdf_5d'] < 10000 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule30_down_avg = rule30_down_sum/rule30_down

        rule31_num = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['zdf_5d'] < 0 and self.now_format_stock_dict[code]['zdf_5d'] > -10 ].__len__()
        rule31_up = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['zdf_5d'] < 0 and self.now_format_stock_dict[code]['zdf_5d'] > -10 and float(self.now_format_stock_dict[code]['zdf'])>0].__len__()
        rule31_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['zdf_5d'] < 0 and self.now_format_stock_dict[code]['zdf_5d'] > -10 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule31_up_avg = rule31_up_sum/rule31_up
        rule31_down = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['zdf_5d'] < 0 and self.now_format_stock_dict[code]['zdf_5d'] > -10 and float(self.now_format_stock_dict[code]['zdf'])<0].__len__()
        rule31_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['zdf_5d'] < 0 and self.now_format_stock_dict[code]['zdf_5d'] > -10 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule31_down_avg = rule31_down_sum/rule31_down

        rule32_num = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['zdf_5d'] < -10 and self.now_format_stock_dict[code]['zdf_5d'] > -20 ].__len__()
        rule32_up = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['zdf_5d'] < -10 and self.now_format_stock_dict[code]['zdf_5d'] > -20 and float(self.now_format_stock_dict[code]['zdf'])>0].__len__()
        rule32_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['zdf_5d'] < -10 and self.now_format_stock_dict[code]['zdf_5d'] > -20 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule32_up_avg = rule32_up_sum/rule32_up
        rule32_down = [ code for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['zdf_5d'] < -10 and self.now_format_stock_dict[code]['zdf_5d'] > -20 and float(self.now_format_stock_dict[code]['zdf'])<0].__len__()
        rule32_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if self.now_format_stock_dict[code]['zdf_5d'] < -10 and self.now_format_stock_dict[code]['zdf_5d'] > -20 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule32_down_avg = rule32_down_sum/rule32_down
        print('[+] 命中 "\033[1;31m60日涨幅>0%%<10%%\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule21_num,rule21_up,rule21_up_avg,rule21_down,rule21_down_avg))
        print('[+] 命中 "\033[1;31m60日涨幅>10%%<30%%\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule22_num,rule22_up,rule22_up_avg,rule22_down,rule22_down_avg))
        print('[+] 命中 "\033[1;31m60日涨幅>30%%<50%%\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule23_num,rule23_up,rule23_up_avg,rule23_down,rule23_down_avg))
        print('[+] 命中 "\033[1;31m60日涨幅>50%%\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule24_num,rule24_up,rule24_up_avg,rule24_down,rule24_down_avg))
        print('[+] 命中 "\033[1;31m60日涨幅<0%% >-10%%\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule25_num,rule25_up,rule25_up_avg,rule25_down,rule25_down_avg))
        print('[+] 命中 "\033[1;31m60日涨幅<-10%% >-30%%\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule26_num,rule26_up,rule26_up_avg,rule26_down,rule26_down_avg))
        print('[+] 命中 "\033[1;31m60日涨幅<-30%% >-50%%\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule27_num,rule27_up,rule27_up_avg,rule27_down,rule27_down_avg))
        print('[+] 命中 "\033[1;31m5日涨幅>0<10%%\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule28_num,rule28_up,rule28_up_avg,rule28_down,rule28_down_avg))
        print('[+] 命中 "\033[1;31m5日涨幅>10%%<20%%\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule29_num,rule29_up,rule29_up_avg,rule29_down,rule29_down_avg))
        print('[+] 命中 "\033[1;31m5日涨幅>20%%\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule30_num,rule30_up,rule30_up_avg,rule30_down,rule30_down_avg))
        print('[+] 命中 "\033[1;31m5日涨幅小于0%%，大于-10%%\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule31_num,rule31_up,rule31_up_avg,rule31_down,rule31_down_avg))
        print('[+] 命中 "\033[1;31m5日涨幅小于-10%%，大于-20%%\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule32_num,rule32_up,rule32_up_avg,rule32_down,rule32_down_avg))


        """
          - 昨日涨幅大于0小于5的股票，今日涨跌情况统计
          - 昨日涨幅大于5的股票，今日涨跌情况统计
          - 昨日涨幅小于0大于-5的股票，今日涨跌情况统计
          - 昨日涨幅小于-5的股票，今日涨跌情况统计
        """
        rule33_num = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_zdf']) > 0 and float(self.now_format_stock_dict[code]['yestoday_zdf']) < 5 ].__len__()
        rule33_up = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_zdf']) > 0 and float(self.now_format_stock_dict[code]['yestoday_zdf']) < 5 and float(self.now_format_stock_dict[code]['zdf'])>0].__len__()
        rule33_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_zdf']) > 0 and float(self.now_format_stock_dict[code]['yestoday_zdf']) < 5 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule33_up_avg = rule33_up_sum/rule33_up
        rule33_down = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_zdf']) > 0 and float(self.now_format_stock_dict[code]['yestoday_zdf']) < 5 and float(self.now_format_stock_dict[code]['zdf'])<0].__len__()
        rule33_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_zdf']) > 0 and float(self.now_format_stock_dict[code]['yestoday_zdf']) < 5 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule33_down_avg = rule33_down_sum/rule33_down

        rule34_num = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_zdf']) > 5 and float(self.now_format_stock_dict[code]['yestoday_zdf']) < 5000 ].__len__()
        rule34_up = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_zdf']) > 5 and float(self.now_format_stock_dict[code]['yestoday_zdf']) < 5000 and float(self.now_format_stock_dict[code]['zdf'])>0].__len__()
        rule34_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_zdf']) > 5 and float(self.now_format_stock_dict[code]['yestoday_zdf']) < 5000 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule34_up_avg = rule34_up_sum/rule34_up
        rule34_down = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_zdf']) > 5 and float(self.now_format_stock_dict[code]['yestoday_zdf']) < 5000 and float(self.now_format_stock_dict[code]['zdf'])<0].__len__()
        rule34_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_zdf']) > 5 and float(self.now_format_stock_dict[code]['yestoday_zdf']) < 5000 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule34_down_avg = rule34_down_sum/rule34_down

        rule35_num = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_zdf']) < 0 and float(self.now_format_stock_dict[code]['yestoday_zdf']) > -5 ].__len__()
        rule35_up = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_zdf']) < 0 and float(self.now_format_stock_dict[code]['yestoday_zdf']) > -5 and float(self.now_format_stock_dict[code]['zdf'])>0].__len__()
        rule35_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_zdf']) < 0 and float(self.now_format_stock_dict[code]['yestoday_zdf']) > -5 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule35_up_avg = rule35_up_sum/rule35_up
        rule35_down = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_zdf']) < 0 and float(self.now_format_stock_dict[code]['yestoday_zdf']) > -5 and float(self.now_format_stock_dict[code]['zdf'])<0].__len__()
        rule35_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_zdf']) < 0 and float(self.now_format_stock_dict[code]['yestoday_zdf']) > -5 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule35_down_avg = rule35_down_sum/rule35_down

        rule36_num = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_zdf']) < -5 and float(self.now_format_stock_dict[code]['yestoday_zdf']) > -100 ].__len__()
        rule36_up = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_zdf']) < -5 and float(self.now_format_stock_dict[code]['yestoday_zdf']) > -100 and float(self.now_format_stock_dict[code]['zdf'])>0].__len__()
        rule36_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_zdf']) < -5 and float(self.now_format_stock_dict[code]['yestoday_zdf']) > -100 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule36_up_avg = rule36_up_sum/rule36_up
        rule36_down = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_zdf']) < -5 and float(self.now_format_stock_dict[code]['yestoday_zdf']) > -100 and float(self.now_format_stock_dict[code]['zdf'])<0].__len__()
        rule36_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_zdf']) < -5 and float(self.now_format_stock_dict[code]['yestoday_zdf']) > -100 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule36_down_avg = rule36_down_sum/rule36_down
        print('[+] 命中 "\033[1;31m昨日涨幅大于0小于5\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule33_num,rule33_up,rule33_up_avg,rule33_down,rule33_down_avg))
        print('[+] 命中 "\033[1;31m昨日涨幅大于5\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule34_num,rule34_up,rule34_up_avg,rule34_down,rule34_down_avg))
        print('[+] 命中 "\033[1;31m昨日涨幅小于0大于-5\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule35_num,rule35_up,rule35_up_avg,rule35_down,rule35_down_avg))
        print('[+] 命中 "\033[1;31m昨日涨幅小于-5\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule36_num,rule36_up,rule36_up_avg,rule36_down,rule36_down_avg))

        """
          - 昨日净流入>0 < 500w的股票, 今日涨跌分布
          - 昨日净流入>500w今日涨跌分布
          - 昨日净流入<0w今日涨跌分布

          - 近五日净流入>0 < 1500w的股票, 今日涨跌分布
          - 近五日净流入>1500w的股票, 今日涨跌分布
          - 近五日净流入<0w的股票, 今日涨跌分布
        """
        rule37_num = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_jlr']) > 0 and float(self.now_format_stock_dict[code]['yestoday_jlr']) < 500 ].__len__()
        rule37_up = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_jlr']) > 0 and float(self.now_format_stock_dict[code]['yestoday_jlr']) < 500 and float(self.now_format_stock_dict[code]['zdf'])>0].__len__()
        rule37_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_jlr']) > 0 and float(self.now_format_stock_dict[code]['yestoday_jlr']) < 500 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule37_up_avg = rule37_up_sum/rule37_up
        rule37_down = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_jlr']) > 0 and float(self.now_format_stock_dict[code]['yestoday_jlr']) < 500 and float(self.now_format_stock_dict[code]['zdf'])<0].__len__()
        rule37_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_jlr']) > 0 and float(self.now_format_stock_dict[code]['yestoday_jlr']) < 500 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule37_down_avg = rule37_down_sum/rule37_down

        rule38_num = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_jlr']) > 500 and float(self.now_format_stock_dict[code]['yestoday_jlr']) < 500000 ].__len__()
        rule38_up = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_jlr']) > 500 and float(self.now_format_stock_dict[code]['yestoday_jlr']) < 500000 and float(self.now_format_stock_dict[code]['zdf'])>0].__len__()
        rule38_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_jlr']) > 500 and float(self.now_format_stock_dict[code]['yestoday_jlr']) < 500000 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule38_up_avg = rule38_up_sum/rule38_up
        rule38_down = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_jlr']) > 500 and float(self.now_format_stock_dict[code]['yestoday_jlr']) < 500000 and float(self.now_format_stock_dict[code]['zdf'])<0].__len__()
        rule38_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_jlr']) > 500 and float(self.now_format_stock_dict[code]['yestoday_jlr']) < 500000 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule38_down_avg = rule38_down_sum/rule38_down

        rule39_num = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_jlr']) < 0 ].__len__()
        rule39_up = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_jlr']) < 0 and float(self.now_format_stock_dict[code]['zdf'])>0].__len__()
        rule39_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_jlr']) < 0 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule39_up_avg = rule39_up_sum/rule39_up
        rule39_down = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_jlr']) < 0 and float(self.now_format_stock_dict[code]['zdf'])<0].__len__()
        rule39_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['yestoday_jlr']) < 0 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule39_down_avg = rule39_down_sum/rule39_down

        rule40_num = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['jlr_5days']) > 0 and float(self.now_format_stock_dict[code]['jlr_5days']) < 1500 ].__len__()
        rule40_up = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['jlr_5days']) > 0 and float(self.now_format_stock_dict[code]['jlr_5days']) < 1500 and float(self.now_format_stock_dict[code]['zdf'])>0].__len__()
        rule40_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['jlr_5days']) > 0 and float(self.now_format_stock_dict[code]['jlr_5days']) < 1500 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule40_up_avg = rule40_up_sum/rule40_up
        rule40_down = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['jlr_5days']) > 0 and float(self.now_format_stock_dict[code]['jlr_5days']) < 1500 and float(self.now_format_stock_dict[code]['zdf'])<0].__len__()
        rule40_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['jlr_5days']) > 0 and float(self.now_format_stock_dict[code]['jlr_5days']) < 1500 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule40_down_avg = rule40_down_sum/rule40_down

        rule41_num = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['jlr_5days']) > 1500 and float(self.now_format_stock_dict[code]['jlr_5days']) < 500000 ].__len__()
        rule41_up = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['jlr_5days']) > 1500 and float(self.now_format_stock_dict[code]['jlr_5days']) < 500000 and float(self.now_format_stock_dict[code]['zdf'])>0].__len__()
        rule41_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['jlr_5days']) > 1500 and float(self.now_format_stock_dict[code]['jlr_5days']) < 500000 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule41_up_avg = rule41_up_sum/rule41_up
        rule41_down = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['jlr_5days']) > 1500 and float(self.now_format_stock_dict[code]['jlr_5days']) < 500000 and float(self.now_format_stock_dict[code]['zdf'])<0].__len__()
        rule41_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['jlr_5days']) > 1500 and float(self.now_format_stock_dict[code]['jlr_5days']) < 500000 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule41_down_avg = rule41_down_sum/rule41_down

        rule42_num = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['jlr_5days']) < 0 ].__len__()
        rule42_up = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['jlr_5days']) < 0 and float(self.now_format_stock_dict[code]['zdf'])>0].__len__()
        rule42_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['jlr_5days']) < 0 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule42_up_avg = rule42_up_sum/rule42_up
        rule42_down = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['jlr_5days']) < 0 and float(self.now_format_stock_dict[code]['zdf'])<0].__len__()
        rule42_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['jlr_5days']) < 0 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule42_down_avg = rule42_down_sum/rule42_down
        print('[+] 命中 "\033[1;31m昨日净流入>0 < 500w\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule37_num,rule37_up,rule37_up_avg,rule37_down,rule37_down_avg))
        print('[+] 命中 "\033[1;31m昨日净流入>500w\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule38_num,rule38_up,rule38_up_avg,rule38_down,rule38_down_avg))
        print('[+] 命中 "\033[1;31m昨日净流入<0w\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule39_num,rule39_up,rule39_up_avg,rule39_down,rule39_down_avg))
        print('[+] 命中 "\033[1;31m近五日净流入>0 < 1500w\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule40_num,rule40_up,rule40_up_avg,rule40_down,rule40_down_avg))
        print('[+] 命中 "\033[1;31m近五日净流入>1500w\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule41_num,rule41_up,rule41_up_avg,rule41_down,rule41_down_avg))
        print('[+] 命中 "\033[1;31m近五日净流入<0w\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule42_num,rule42_up,rule42_up_avg,rule42_down,rule42_down_avg))

        """
          - 评分>85的，今日涨跌分布
          - 评分<85的，今日涨跌分布

          - 主力增仓的，今日涨跌分布1
          - 主力减仓的，今日涨跌分布0
          - 主力中立的，今日涨跌分布2

          - 行业增仓的，今日涨跌分布
          - 行业减仓的，今日涨跌分布

          - 5日线上的, 今日涨跌幅分布
          - 5日线下的, 今日涨跌幅分布

        """

        rule43_num = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['score']) > 85 ].__len__()
        rule43_up = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['score']) > 85 and float(self.now_format_stock_dict[code]['zdf'])>0].__len__()
        rule43_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['score']) > 85 and float(self.now_format_stock_dict[code]['zdf'])>0])
        try:
            rule43_up_avg = rule43_up_sum/rule43_up
        except:
            rule43_up_avg = 0
        rule43_down = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['score']) > 85 and float(self.now_format_stock_dict[code]['zdf'])<0].__len__()
        rule43_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['score']) > 85 and float(self.now_format_stock_dict[code]['zdf'])<0])
        try:
            rule43_down_avg = rule43_down_sum/rule43_down
        except:
            rule43_down_avg = 0

        rule44_num = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['score']) < 85 ].__len__()
        rule44_up = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['score']) < 85 and float(self.now_format_stock_dict[code]['zdf'])>0].__len__()
        rule44_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['score']) < 85 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule44_up_avg = rule44_up_sum/rule44_up
        rule44_down = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['score']) < 85 and float(self.now_format_stock_dict[code]['zdf'])<0].__len__()
        rule44_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['score']) < 85 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule44_down_avg = rule44_down_sum/rule44_down

        rule45_num = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['zjdx1']) == 1 ].__len__()
        rule45_up = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['zjdx1']) == 1 and float(self.now_format_stock_dict[code]['zdf'])>0].__len__()
        rule45_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['zjdx1']) == 1 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule45_up_avg = rule45_up_sum/rule45_up
        rule45_down = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['zjdx1']) == 1 and float(self.now_format_stock_dict[code]['zdf'])<0].__len__()
        rule45_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['zjdx1']) == 1 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule45_down_avg = rule45_down_sum/rule45_down

        rule46_num = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['zjdx1']) == 0 ].__len__()
        rule46_up = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['zjdx1']) == 0 and float(self.now_format_stock_dict[code]['zdf'])>0].__len__()
        rule46_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['zjdx1']) == 0 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule46_up_avg = rule46_up_sum/rule46_up
        rule46_down = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['zjdx1']) == 0 and float(self.now_format_stock_dict[code]['zdf'])<0].__len__()
        rule46_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['zjdx1']) == 0 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule46_down_avg = rule46_down_sum/rule46_down

        rule47_num = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['zjdx1']) == 2 ].__len__()
        rule47_up = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['zjdx1']) == 2 and float(self.now_format_stock_dict[code]['zdf'])>0].__len__()
        rule47_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['zjdx1']) == 2 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule47_up_avg = rule47_up_sum/rule47_up
        rule47_down = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['zjdx1']) == 2 and float(self.now_format_stock_dict[code]['zdf'])<0].__len__()
        rule47_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['zjdx1']) == 2 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule47_down_avg = rule47_down_sum/rule47_down

        rule48_num = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['hydx1']) == 1 ].__len__()
        rule48_up = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['hydx1']) == 1 and float(self.now_format_stock_dict[code]['zdf'])>0].__len__()
        rule48_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['hydx1']) == 1 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule48_up_avg = rule48_up_sum/rule48_up
        rule48_down = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['hydx1']) == 1 and float(self.now_format_stock_dict[code]['zdf'])<0].__len__()
        rule48_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['hydx1']) == 1 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule48_down_avg = rule48_down_sum/rule48_down

        rule49_num = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['hydx1']) == 0 ].__len__()
        rule49_up = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['hydx1']) == 0 and float(self.now_format_stock_dict[code]['zdf'])>0].__len__()
        rule49_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['hydx1']) == 0 and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule49_up_avg = rule49_up_sum/rule49_up
        rule49_down = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['hydx1']) == 0 and float(self.now_format_stock_dict[code]['zdf'])<0].__len__()
        rule49_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['hydx1']) == 0 and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule49_down_avg = rule49_down_sum/rule49_down

        rule50_num = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['trade']) >= float(self.now_format_stock_dict[code]['ma5']) ].__len__()
        rule50_up = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['trade']) >= float(self.now_format_stock_dict[code]['ma5']) and float(self.now_format_stock_dict[code]['zdf'])>0].__len__()
        rule50_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['trade']) >= float(self.now_format_stock_dict[code]['ma5']) and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule50_up_avg = rule50_up_sum/rule50_up
        rule50_down = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['trade']) >= float(self.now_format_stock_dict[code]['ma5']) and float(self.now_format_stock_dict[code]['zdf'])<0].__len__()
        rule50_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['trade']) >= float(self.now_format_stock_dict[code]['ma5']) and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule50_down_avg = rule50_down_sum/rule50_down

        rule51_num = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['trade']) < float(self.now_format_stock_dict[code]['ma5']) ].__len__()
        rule51_up = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['trade']) < float(self.now_format_stock_dict[code]['ma5']) and float(self.now_format_stock_dict[code]['zdf'])>0].__len__()
        rule51_up_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['trade']) < float(self.now_format_stock_dict[code]['ma5']) and float(self.now_format_stock_dict[code]['zdf'])>0])
        rule51_up_avg = rule51_up_sum/rule51_up
        rule51_down = [ code for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['trade']) < float(self.now_format_stock_dict[code]['ma5']) and float(self.now_format_stock_dict[code]['zdf'])<0].__len__()
        rule51_down_sum = sum([ self.now_format_stock_dict[code]['zdf'] for code in self.now_format_stock_dict if float(self.now_format_stock_dict[code]['trade']) < float(self.now_format_stock_dict[code]['ma5']) and float(self.now_format_stock_dict[code]['zdf'])<0])
        rule51_down_avg = rule51_down_sum/rule51_down
        print('[+] 命中 "\033[1;31m评分>85\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule43_num,rule43_up,rule43_up_avg,rule43_down,rule43_down_avg))
        print('[+] 命中 "\033[1;31m评分<85\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule44_num,rule44_up,rule44_up_avg,rule44_down,rule44_down_avg))
        print('[+] 命中 "\033[1;31m主力增仓\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule45_num,rule45_up,rule45_up_avg,rule45_down,rule45_down_avg))
        print('[+] 命中 "\033[1;31m主力减仓\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule46_num,rule46_up,rule46_up_avg,rule46_down,rule46_down_avg))
        print('[+] 命中 "\033[1;31m主力中立\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule47_num,rule47_up,rule47_up_avg,rule47_down,rule47_down_avg))
        print('[+] 命中 "\033[1;31m行业增仓\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule48_num,rule48_up,rule48_up_avg,rule48_down,rule48_down_avg))
        print('[+] 命中 "\033[1;31m行业减仓\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule49_num,rule49_up,rule49_up_avg,rule49_down,rule49_down_avg))
        print('[+] 命中 "\033[1;31m5日线上\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule50_num,rule50_up,rule50_up_avg,rule50_down,rule50_down_avg))
        print('[+] 命中 "\033[1;31m5日线下\033[0m" 的股票 %s 支, 其中上涨 %s[%.2f%%] 支, 下跌 %s[%.2f%%] 支' % (rule51_num,rule51_up,rule51_up_avg,rule51_down,rule51_down_avg))

    def market_data_analyse(self):
        # 1. 加载个股分析数据集
        self.get_all_code()
        self.get_anaylse_data()
        
        # 2. 加载均线数据集
        self.get_jx_data()

        # 3. 获取当前实时股票行情指标
        ntime = "\033[1;32m%s\033[0m" % str(time.strftime('%H:%M:%S' , time.localtime()))
        fh = "\033[1;37m+\033[0m"
        print("[%s][%s] 获取实时股票数据中, 请稍后..." % (fh, ntime))
        print("_"*150)
        self.format_realtime_data()

        # 4. 数据整合
        for i in self.stock_jx_data:
            code = i
            if code in self.now_format_stock_dict.keys():
                self.now_format_stock_dict[code]['jx_data'] = self.stock_jx_data[i]

        # 5. 市场分析
        ntime = "\033[1;32m%s\033[0m" % str(time.strftime('%H:%M:%S' , time.localtime()))
        fh = "\033[1;37m+\033[0m"
        print("[%s][%s] 市场数据分析中, 请稍后..." % (fh, ntime))
        self.market_analyse()
        print("_"*150)

    def main(self):
        if not os.path.exists("./config/settings.conf"):
            print("[-] settings.conf is not found, pls check it!")
            sys.exit()
        else:
            parser = OptionParser()

            parser.add_option("--code", dest="code", default=False, help=u"查看指定股票, --code 000001 或 --code 000001,000002")

            parser.add_option("--format_result", dest="format_result", default=False, help=u"查看结果, --format_result result/20210129_money_flow.txt")

            parser.add_option("--conditions_filter", action="store_true", dest="conditions_filter", default=False, help=u"是否进行过滤条件筛选")

            parser.add_option("--add2zx", dest="add2zx", default=False, help=u"添加到东方财富自选, --add2zx result/2021-02-03/20210129_money_flow.txt")

            parser.add_option("--format_loop", action="store_true", dest="format_loop", default=False, help=u"是否循环查看异动股票")

            parser.add_option("--format_all", action="store_true", dest="format_all", default=False, help=u"是否查看所有异动股票(包含下跌)")

            parser.add_option("--market_data_analyse", action="store_true", dest="market_data_analyse", default=False, help=u"进行市场分析")

            (options, args) = parser.parse_args()

            if args:
                parser.print_help()
            else:
                if datetime.datetime.now().weekday()+1 in [6, 7]:
                    return

                # 查看当前股票情况
                if options.format_result:
                    result_file = options.format_result
                    self.conditions_filter = options.conditions_filter

                    if options.format_loop:
                        self.format_loop = options.format_loop
                    else:
                        self.format_loop = False

                    if options.format_all:
                        self.format_all = options.format_all
                    else:
                        self.format_all = False

                    self.format_result(result_file, parser)

                #  增加自选股到东方财富
                elif options.add2zx:
                    result_file = options.add2zx
                    self.add2zx(result_file)

                # 市场数据分析
                elif options.market_data_analyse:
                    self.market_data_analyse()

                else:
                    self.work(code=options.code)

if __name__ == "__main__":

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

    # 微信提醒
    is_notify = eval(config.get('base', 'is_notify'))  # 是否发送微信提醒

    # Cookies
    cookie = config.get('user', 'cookie')

    # appkey
    appkey = config.get('user', 'appkey')

    # 实例化
    sn = StockNet(token, is_limit, limit_num, is_notify, is_zxg_monitor, zxg_list, cookie, appkey)

    # 开始工作
    sn.main()




