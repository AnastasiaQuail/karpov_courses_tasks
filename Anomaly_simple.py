import telegram
import io
import re

import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd
from datetime import datetime, timedelta, date
import matplotlib.dates as mdates
import pandahouse as ph

from airflow.decorators import dag, task
from airflow.operators.python import get_current_context

# Подключаемся к боту
my_token = '6093396672:AAHLmoQ6ug3ydEM3n_243YZN9djEdLtjmbM' 
bot = telegram.Bot(token=my_token) # получаем доступ

# Подключение к схеме с данными для выгрузок
connection = {'host': 'https://clickhouse.lab.karpov.courses',
                      'database':'simulator_20230120',
                      'user':'student', 
                      'password':'dpo_python_2020'
                     }

# Дефолтные параметры, которые прокидываются в таски
default_args = {
    'owner': 'a-perepelova',
    'depends_on_past': False,
    'retries': 2,
    'retry_delay': timedelta(minutes=5),
    'start_date': datetime(2023, 2, 13),
}

# Интервал запуска DAG
schedule_interval = '*/15 * * * *'

# Функция проверки на аномалии с помощью 
# интерквартильного размаха
def anomaly_check(df, metric,n=5, a=4):
    # определим верхний и нижний пороги
    # a - коэффициент, n - кол-во 15-минуток до, по которым считаем пороги
    sns.set(rc={'figure.figsize':(11.7,8.27)})
        
    df['q75'] = df[metric].shift(1).rolling(n).quantile(0.75)
    df['q25'] = df[metric].shift(1).rolling(n).quantile(0.25)
    df['iqr'] = df['q75'] - df['q25']
    df['lower'] = df['q25'] - a*df['iqr']
    df['upper'] = df['q75'] + a*df['iqr']
        
    df['lower'] = df['lower'].rolling(7,center=True, min_periods=1).mean()
    df['upper'] = df['upper'].rolling(7,center=True, min_periods=1).mean()
    df['is_alert'] = 0
        
    #проверим, выходит ли метрика за них
    if df[metric].iloc[-1]>df['upper'].iloc[-1] or df[metric].iloc[-1]<df['lower'].iloc[-1]:
        is_alert = 1
        df['is_alert'].iloc[-1] = 1
    else:
        is_alert = 0
        
    return is_alert,df

@dag(default_args=default_args, schedule_interval=schedule_interval, catchup=False)
def perepelova_anomaly_dag(chat_id = 427505265):
    '''
    Каждые 15 минут dag сканирует данные на наличие аномалий и
    в случае проблем отправляет уведомление в ТГ чат. 
    Данные, которые анализируются:
    - активные пользователи в ленте / мессенджере,
    - просмотры,
    - лайки,
    - CTR,
    - количество отправленных сообщений
    '''

    # Выгружаем данные
    @task
    def extract():
        query = '''
                with feeds as
                (SELECT 
                        toStartOfFifteenMinutes(time) as ts,
                        toDate(time) as dt,
                        formatDateTime(ts,'%R') as hm,
                        count(distinct user_id) as DAUfeed,
                        countIf(user_id, action = 'view') as views,
                        countIf(user_id, action = 'like') as likes,
                        round(likes*100/views,2) as CTR
                    FROM 
                        simulator_20230120.feed_actions  
                    where  toStartOfFifteenMinutes(time)!= toStartOfFifteenMinutes(now())
                    and time >= today() - 7
                    group by ts, dt, hm
                    order by ts),
                msg as
                (SELECT
                        toStartOfFifteenMinutes(time) as ts,
                        toDate(time) as dt,
                        formatDateTime(ts,'%R') as hm,
                        count(distinct user_id) as DAUmsg,
                        count(user_id) as messages
                    FROM 
                        simulator_20230120.message_actions
                    where toStartOfFifteenMinutes(time)!= toStartOfFifteenMinutes(now())
                    and time >= today() - 7
                    group by ts, dt, hm
                    order by ts)
                SELECT feeds.ts,
                        feeds.dt,
                        feeds.hm,
                        DAUfeed,
                        DAUmsg,
                        views,
                        likes,
                        CTR,
                        messages
                from feeds
                left join msg  using(ts,dt,hm)
                
                '''
        df = ph.read_clickhouse(query, connection=connection)
        return df
        
    
    # Отправка сообщения в ТГ в случае нахождения аномалий
    @task
    def send_alert(df, chat_id = 427505265):
        metric_list = ['DAUfeed','DAUmsg','views','likes', 'CTR','messages']
        tm = df.ts.iloc[-1].strftime('%y-%m-%d')
        palette ={"upper": "tab:green", "lower": "tab:green"}
        
        for metric in metric_list:
            print(metric)
            data = df[['ts','dt','hm',metric]].copy()
            is_alert,df_metric = anomaly_check(data, metric)
            print(df_metric.tail())
            if df_metric['is_alert'].iloc[-1]==1:
                diff = round((df_metric[metric].iloc[-1]/df_metric[metric].iloc[-2]-1)*100,2)
                msg = f''' 
⚠️Метрика *{re.escape(metric)}* за *{re.escape(tm)}*
Текущее значение {df_metric[metric].iloc[-1]:.0f}
Отклонение более {re.escape(diff.astype('str'))}%
                '''
                print(msg)
                bot.sendMessage(chat_id = chat_id, text=msg, parse_mode='MarkdownV2')
            
                ax = sns.lineplot(data = df_metric, x='ts', y=metric)
                ax = sns.lineplot(data = df_metric, x='ts', y='upper')
                ax = sns.lineplot(data = df_metric, x='ts', y='lower')
                ax = sns.scatterplot(data = df_metric[df_metric['is_alert']==1], x='ts', y=metric,
                                     palette="flare", hue='is_alert', legend = False)
                plt.title(f'Метрика {metric} за последние сутки')
                # Define the date format
                ax.xaxis.set_major_formatter(mdates.DateFormatter('%H-%M'))
                plt.xticks(rotation=20)

                # Сохраняем в буфер
                plot_object = io.BytesIO()
                plt.savefig(plot_object)
                plot_object.seek(0)
                plot_object.name = f'{metric}_plot.png'
                plt.show()
                plt.close()
                bot.sendPhoto(chat_id=chat_id, photo=plot_object)
        
    df = extract()
    send_alert(df,chat_id=chat_id)
    return df

df = perepelova_anomaly_dag(-520311152) 
