cd /opt/app/stock_realtime_anaysis/
d_1=`python -c 'import time;print time.strftime("%Y-%m-%d", time.localtime())'`
d_2=`python -c 'import time;print time.strftime("%Y%m%d", time.localtime())'`
d_3=`python -c 'import time;print time.strftime("%H_%M_%S", time.localtime())'`

mkdir result/$d_1/market_anaylse &>> /dev/null  2>&1 &

python filter_good_stock_pro.py --market_data_analyse > result/$d_1/market_anaylse/stock_data_analyse\_$d_3.txt
