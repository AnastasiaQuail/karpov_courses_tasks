import telegram
import io

import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd
from datetime import datetime, timedelta
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
    'start_date': datetime(2023, 2, 9),
}

# Интервал запуска DAG
schedule_interval = '0 11 * * *'

@dag(default_args=default_args, schedule_interval=schedule_interval, catchup=False)
def perepelova_1report_dag(chat_id = 427505265):
    
    @task
    def extract_data():
        query = """SELECT 
                        toDate(time) as event_date,
                        count(distinct user_id) as DAU,
                       countIf(user_id, action = 'view') as views,
                       countIf(user_id, action = 'like') as likes,
                       round(likes*100/views,2) as CTR
                    FROM 
                        simulator_20230120.feed_actions  
                    where 
                        toDate(time) between today() - 7 and today() - 1
                    group by event_date
                    """
        feed_info = ph.read_clickhouse(query, connection=connection)
        return feed_info
    
    @task
    def send_text(df, chat_id = 427505265):
        ds = (datetime.today() - timedelta(days=1))
        
        df_yesterday = df[df['event_date']==ds.strftime("%Y-%m-%d")]
        msg = f'''
🚨Отчет за *{ds.strftime("%Y/%m/%d")}*\n
*DAU:* {df_yesterday['DAU'].values[0]:,}\n
*Likes:* {df_yesterday['likes'].values[0]:,}\n
*Views:* {df_yesterday['views'].values[0]:,}\n
*CTR:* {df_yesterday['CTR'].values[0]:.0f}%\n
                '''
        print(msg)
        bot.sendMessage(chat_id=chat_id, text=msg, parse_mode='MarkdownV2')
    
    @task
    def send_plot(df,metric='DAU', chat_id = 427505265):
        
        ax = sns.lineplot(df['event_date'], df[metric])
        plt.title(f'{metric} за последние 7 дней')
        # Define the date format
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%d/%m'))
        plt.xticks(rotation=20)
        
        # Сохраняем в буфер
        plot_object = io.BytesIO()
        plt.savefig(plot_object)
        plot_object.seek(0)
        plot_object.name = f'{metric}_plot.png'
        plt.show()
        plt.close()
        bot.sendPhoto(chat_id=chat_id, photo=plot_object)
    
    df = extract_data()
    send_text(df, chat_id=chat_id)
    send_plot(df, metric = 'DAU', chat_id=chat_id)
    send_plot(df, metric = 'likes', chat_id=chat_id)
    send_plot(df, metric = 'views', chat_id=chat_id)
    send_plot(df, metric = 'CTR', chat_id=chat_id)

report = perepelova_1report_dag(chat_id=-677113209)
