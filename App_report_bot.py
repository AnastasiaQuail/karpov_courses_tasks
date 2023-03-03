import telegram
import io
import re

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
def perepelova_2report_dag(chat_id = 427505265):
    '''
    Собирает основные метрики приложения и отправляет их в telegram чат.
    Метрики за вчера:
        - DAU общее (сравнение с прошлым днем)
        - New users (сравнение с прошлым днем):
                organic, ad
        
        - Кол-во 
            - просмотров (на пользователя \ пост)
            - лайков (на пользователя\пост)
            - сообщений (на пользователя)
            - СТR 
    Динамика метрик:
        - динамика аудитории(новые/ушедшие/оставшиеся)
        - динамика аудитории по использованию сервиса
        - динамика событий на пользователя
    '''
    
    # Выгружаем аггрегированные метрики
    @task
    def extract_totals():
        query = """
                with feeds as
                (SELECT 
                        toDate(time) as event_date,
                        count(distinct user_id) as DAU_feed,
                        countIf(user_id, action = 'view') as views,
                        countIf(user_id, action = 'like') as likes,
                        round(likes*100/views,2) as CTR
                    FROM 
                        simulator_20230120.feed_actions  
                    where 
                        toDate(time) between today() - 7 and today() - 1
                    group by event_date),
                msg as
                (SELECT
                        toDate(time) as event_date,
                        count(distinct user_id) as DAU_msg,
                        count(user_id) as messages
                    FROM 
                        simulator_20230120.message_actions
                    where 
                        toDate(time) between today() - 7 and today() - 1
                    group by event_date),
                totals as
                (SELECT event_date, count(distinct user_id) as DAU
                from
                (SELECT 
                        toDate(time) as event_date,
                        user_id
                    FROM 
                        simulator_20230120.feed_actions
                        where 
                        toDate(time) between today() - 7 and today() - 1
                    UNION ALL
                SELECT
                        toDate(time) as event_date,
                        user_id
                    FROM 
                        simulator_20230120.message_actions
                        where 
                        toDate(time) between today() - 7 and today() - 1) as t
                        group by event_date)
                SELECT totals.event_date as event_date,
                        DAU,
                        DAU_feed,
                        DAU_msg,
                        views,
                        views/DAU as user_views,
                        likes,
                        likes/DAU as user_likes,
                        CTR,
                        messages,
                        messages/DAU as user_msg
                from totals
                left join feeds using(event_date)
                left join msg on msg.event_date = totals.event_date
                    
                    """
        df = ph.read_clickhouse(query, connection=connection)
        return df
    
    # считаем разницу показателей с днем до вчера
    @task
    def transform_totals(df):
        df.set_index('event_date', inplace=True)
        df_diff = round((df - df.shift(1))*100/df.shift(1),1)
        df_diff = df_diff.dropna()
        df_diff = df_diff.astype('str')
        df_diff = df_diff.applymap(lambda x: x + '%').reset_index()
        return df_diff
    
    #Выгружаем метрики для графиков
    @task
    def services_data():
        query = """SELECT dt as event_date,
                           replaceRegexpAll(toString(services),'\[|\]|\"]','') AS services,
                           count(DISTINCT user_id) AS users
                FROM
                  (with src AS (
                                  (SELECT DISTINCT user_id,
                                                   toDate(time) as dt,
                                                   'feed' as service
                                   from simulator_20230120.feed_actions)
                                union all
                                  (SELECT DISTINCT user_id,
                                                   toDate(time) as dt,
                                                   'message' as service
                                   from simulator_20230120.message_actions))
                select *
                   from
                     (SELECT T.dt,
                             groupUniqArray(service) as services,
                             T.user_id
                      from
                        (select DISTINCT user_id,
                                         dt
                         from src where dt between today() - 7 and today() - 1) as T
                      left join
                        (select distinct user_id,
                                         service,
                                         dt
                         from src) as S on S.user_id = T.user_id
                      where S.dt <= T.dt
                      group by T.user_id,
                               T.dt) as db) AS virtual_table
                GROUP BY services,
                         dt"""
        df_services = ph.read_clickhouse(query, connection=connection)
        return df_services
    
    # Состав аудитории по группам: ушедшие, новые, оставшиеся, вернувшиеся
    @task
    def auditory_data():
        query_au = """
        SELECT current_day as event_date,status, users
        from
                    (SELECT current_day,
                      previous_day,
                      status, -uniqExact(user_id) as users
               FROM
                 (SELECT user_id,
                         groupUniqArray(toDate(time)) as active_days,
                         addDays(arrayJoin(active_days), 1) as current_day,
                         subtractDays(current_day, 1) as previous_day,
                         if(has(active_days, current_day)=1, 'retained', 'gone') as status
                  from simulator_20230120.feed_actions
                  group by user_id) as t1
               where status = 'gone'
               group by current_day,
                        previous_day,
                        status
               UNION ALL SELECT current_day,
                                previous_day,
                                status,
                                toInt64(uniqExact(user_id)) as users
               FROM
                 (SELECT user_id,
                         groupUniqArray(toDate(time)) as active_days,
                         arrayJoin(active_days) as current_day,
                         subtractDays(current_day, 1) as previous_day,
                         if(has(active_days, previous_day)=1, 'retained', if(length(active_days)=1, 'new', 'returned')) as status
                  from simulator_20230120.feed_actions
                  group by user_id) as t2
               group by current_day,
                        previous_day,
                        status) AS virtual_table
            WHERE current_day between today() - 7 and today() - 1
            
                """
        df_au = ph.read_clickhouse(query_au, connection=connection)
        return df_au
     
    # Данные по ретеншну   
    @task
    def retention_data():
        query_ret = """
                    with tb as
                    (SELECT DISTINCT  user_id,
                                      toDate(time) as day,
                                      source
                                         from simulator_20230120.feed_actions
                                         union all 
                                         SELECT DISTINCT  user_id,
                                                toDate(time) as day,
                                                source
                                         from simulator_20230120.message_actions),
                    regs as 
                    (SELECT user_id,
                            argMin(source, day) as source,
                            min(day) as reg_time
                          from tb
                        group by user_id)
                  SELECT ret.reg_time,
                          source,
                          day_to,
                          100*ret.users/totals.users as retention
                  from
                    (SELECT  reg_time,
                            source,
                            datediff('day',reg_time, day) as day_to,
                            count(distinct user_id) as users
                      from tb
                      INNER JOIN regs using(user_id)
                      where 
                      reg_time in (yesterday() -30, yesterday() -7, yesterday() - 1, yesterday())
                      and day =yesterday()
                      GROUP by day_to,
                                reg_time,
                                source) as ret
                                left join 
                          (SELECT reg_time, source, count(distinct user_id) as users 
                          from regs 
                          group by reg_time,source) as totals
                          using(reg_time,source)
                    """
        df_ret = ph.read_clickhouse(query_ret, connection=connection)
        return df_ret
    
    @task
    def send_text(df_totals, df_diffs, df_au,df_ret,chat_id = 427505265):
        ds = (datetime.today() - timedelta(days=1))
        df_totals.reset_index(inplace=True)

        # Получаем данные за вчера
        df_yesterday = df_totals[df_totals['event_date']==ds.strftime("%Y-%m-%d")]
        df_diffs = df_diffs[df_diffs['event_date']==(datetime.today() - timedelta(days=2)).strftime("%Y-%m-%d")]
        new_users = df_au[(df_au['event_date']==ds.strftime("%Y-%m-%d"))&
                     (df_au['status'] =='new')]['users'].values[0]
        ret_ad = df_ret[df_ret['source']=='ads'][['day_to','retention']]
        ret_org = df_ret[df_ret['source']=='organic'][['day_to','retention']]
        
        msg =  re.escape(f'''
🚨Ключевые метрики **Приложения** за *{ds.strftime("%Y/%m/%d")}* 

👥Аудиторные данные:  
*DAU общее:* {df_yesterday['DAU'].values[0]:,} ({df_diffs['DAU'].values[0]})
        DAU лента: {df_yesterday['DAU_feed'].values[0]:,} ({df_diffs['DAU_feed'].values[0]})
        DAU мессенджер: {df_yesterday['DAU_msg'].values[0]:,} ({df_diffs['DAU_msg'].values[0]})
*New users:* {new_users:,}

📌События:
*Views:* {df_yesterday['views'].values[0]:,} ({df_diffs['views'].values[0]})
*Likes:* {df_yesterday['likes'].values[0]:,} ({df_diffs['likes'].values[0]})
*CTR:* {df_yesterday['CTR'].values[0]:.1f}% ({df_diffs['CTR'].values[0]})
*Messages:* {df_yesterday['messages'].values[0]:,} ({df_diffs['messages'].values[0]})

*Organic* Ret 1: {ret_org[ret_org['day_to']==1]['retention'].values[0]:.1f} Ret 7: {ret_org[ret_org['day_to']==7]['retention'].values[0]:.1f} Ret 30: {ret_org[ret_org['day_to']==30]['retention'].values[0]:.1f}
*Ads* Ret 1: {ret_ad[ret_ad['day_to']==1]['retention'].values[0]:.1f} Ret 7: {ret_ad[ret_ad['day_to']==7]['retention'].values[0]:.1f} Ret 30: {ret_ad[ret_ad['day_to']==30]['retention'].values[0]:.1f}
                ''').replace('\*','*')
        print(msg)
        bot.sendMessage(chat_id=chat_id, text=msg, parse_mode='MarkdownV2')
        links = '''
        🤓Подробнее в отчетах:
        [Приложение \- основное](https://superset.lab.karpov.courses/superset/dashboard/2660/)
        [Лента новостей \- основное](https://superset.lab.karpov.courses/superset/dashboard/2608/)
        [Ретеншн](https://superset.lab.karpov.courses/superset/dashboard/2828/)'''
        bot.sendMessage(chat_id = chat_id, text = links,parse_mode='MarkdownV2')
    
    #динамика событий на пользователя
    @task
    def send_line(df, chat_id = 427505265):
        data = df[['event_date','user_views','user_likes','user_msg']].melt(id_vars='event_date')
        # Динамика метрик на пользователя по дням
        ax = sns.lineplot(data = data, x='event_date', y='value',hue='variable',  palette="flare")
        plt.title(f'Метрики на пользователя за последние 7 дней')
        # Define the date format
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%d/%m'))
        plt.xticks(rotation=20)
        
        # Сохраняем в буфер
        plot_object = io.BytesIO()
        plt.savefig(plot_object)
        plot_object.seek(0)
        plot_object.name = f'actions_plot.png'
        plt.show()
        plt.close()
        bot.sendPhoto(chat_id=chat_id, photo=plot_object)
        
    #Данные по сегментам аудитории
    @task
    def send_auditory_split(df, chat_id = 427505265):
        data = df.pivot(index = 'event_date', columns = 'status').reset_index()
        ax = data.plot( kind='bar', stacked=True, x='event_date', y='users')
        plt.title(f'Аудитория за последние 7 дней')
        # Define the date format
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%d/%m'))
        plt.xticks(rotation=20)
        
        # Сохраняем в буфер
        plot_object = io.BytesIO()
        plt.savefig(plot_object)
        plot_object.seek(0)
        plot_object.name = f'actions_plot.png'
        plt.show()
        plt.close()
        bot.sendPhoto(chat_id=chat_id, photo=plot_object)
        
    #Данные по использованию сервисов
    @task
    def send_services_split(df, chat_id = 427505265):
        data = df.pivot(index = 'event_date', columns = 'services').reset_index()
        ax = data.plot( kind='bar', stacked=True, x='event_date', y='users')
        ax.legend(loc = 'lower left')
        plt.title(f'Аудитория по использованию сервисов за последние 7 дней')
        # Define the date format
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%d/%m'))
        plt.xticks(rotation=20)
        
        # Сохраняем в буфер
        plot_object = io.BytesIO()
        plt.savefig(plot_object)
        plot_object.seek(0)
        plot_object.name = f'actions_plot.png'
        plt.show()
        plt.close()
        bot.sendPhoto(chat_id=chat_id, photo=plot_object)
        
    
    df_totals = extract_totals()
    df_diffs = transform_totals(df_totals)
    df_au = auditory_data()
    df_ret = retention_data()
    df_services = services_data()
    send_text(df_totals, df_diffs,df_au,df_ret,chat_id=chat_id)
    
    send_line(df_totals,chat_id=chat_id)
    send_auditory_split(df_au,chat_id=chat_id)
    send_services_split(df_services,chat_id=chat_id)
    

report = perepelova_2report_dag(chat_id=-677113209)
