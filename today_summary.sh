d_1=`python -c 'import time;print time.strftime("%Y-%m-%d", time.localtime())'`
d_2=`python -c 'import time;print time.strftime("%Y%m%d", time.localtime())'`
cd /opt/app/stock_realtime_anaysis/ && python filter_good_stock_pro.py --format_result result/$d_1/$d_2\_money_flow.txt --format_all > result/$d_1/$d_2\_zongjie.txt
