from datetime import datetime, timedelta
import pandas as pd
import pandahouse as ph

from airflow.decorators import dag, task
from airflow.operators.python import get_current_context

# Создание таблицы 
connection_test = {'host': 'https://clickhouse.lab.karpov.courses',
                      'database':'test',
                      'user':'student-rw', 
                      'password':'656e2b0c9c'
                     }
create_query = '''
            CREATE TABLE IF NOT EXISTS test.a_perepelova_test (
                  event_date Date,
                  dimension String,
                  dimension_value String,
                  views Float64,
                  likes Float64,
                  messages_received Float64,
                  messages_sent Float64,
                  users_received Float64,
                  users_sent Float64
                ) ENGINE = MergeTree()
                ORDER BY
                  event_date
            '''
ph.execute(query = create_query, connection = connection_test)

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
    'start_date': datetime(2023, 2, 8),
}

# Интервал запуска DAG
schedule_interval = '0 23 * * *'

@dag(default_args=default_args, schedule_interval=schedule_interval, catchup=False)
def perepelova_ETL():

    # Данные о лайках и просмотрах по каждому пользователю
    @task()
    def extract_feeds():
        query = """SELECT 
                        toDate(time) as event_date,
                       user_id,
                        os,
                        gender,
                        age,
                       countIf(user_id, action = 'view') as views,
                       countIf(user_id, action = 'like') as likes
                    FROM 
                        simulator_20230120.feed_actions  
                    where 
                        toDate(time) = yesterday()
                    group by event_date,
                            user_id,
                            os,
                            gender,
                            age"""
        feed_info = ph.read_clickhouse(query, connection=connection)
        return feed_info
    
     # Данные по переписках по каждому пользователю
    @task()
    def extract_messages():
        query = """with sent as (
          SELECT
            toDate(time) as event_date,
            user_id,
            os,
            gender,
            age,
            count(distinct reciever_id) as users,
            count(reciever_id) as messages
          FROM
            simulator_20230120.message_actions
          where
            toDate(time) = yesterday()
          group by
            event_date,
            user_id,
            os,
            gender,
            age
        ),
        receive as (
          SELECT
            toDate(time) as event_date,
            reciever_id,
            count(distinct user_id) as users,
            count(user_id) as messages
          FROM
            simulator_20230120.message_actions
          where
            toDate(time) = yesterday()
          group by
            reciever_id,
            event_date
        )
        select
          coalesce(sent.event_date, receive.event_date) as event_date,
          coalesce(sent.user_id, receive.reciever_id) as user_id,
          os,
          gender,
          age,
          receive.messages as messages_received,
          sent.messages as messages_sent,
          receive.users as users_received,
          sent.users as users_sent
        from
          sent full
          join receive on receive.reciever_id = sent.user_id
                          and  receive.event_date = sent.event_date
                          """
        message_info = ph.read_clickhouse(query, connection=connection)
        return message_info

    #объединяем результаты двух таблиц в одну
    @task
    def transfrom_merge(feed_info,message_info):
        df = feed_info.merge(message_info, on=['user_id','event_date','gender','os','age'], how='outer')
        return df

    # Группируем данные по полу
    @task
    def transfrom_gender(df):
        df_gender = df\
            .groupby(['event_date', 'gender'])\
            .sum()\
            .reset_index()
        df_gender['dimension'] = 'gender'
        df_gender.rename(columns = {'gender':'dimension_value'},inplace = True)
        df_gender = df_gender[['event_date',
                  'dimension',
                  'dimension_value',
                  'views',
                  'likes',
                  'messages_received',
                  'messages_sent',
                  'users_received',
                  'users_sent']]
        return df_gender
    
    # Группируем данные по OS
    @task
    def transfrom_os(df):
        df_os = df\
            .groupby(['event_date', 'os'])\
            .sum()\
            .reset_index()
        df_os['dimension'] = 'os'
        df_os.rename(columns = {'os':'dimension_value'},inplace = True)
        df_os = df_os[['event_date',
                  'dimension',
                  'dimension_value',
                  'views',
                  'likes',
                  'messages_received',
                  'messages_sent',
                  'users_received',
                  'users_sent']]
        return df_os
    
    # Группируем данные по возрасту
    @task
    def transfrom_age(df):
        # Создадим группы пользователей по возрасту
        df['age_group'] = pd.cut(df['age'], bins = [0 ,18 , 25, 30, 45, 100],
                                            labels =['До 18', '18-25', '25-30', '30-45', '45+'])
        df.drop(columns = ['age'],inplace=True)
        df_age = df\
            .groupby(['event_date', 'age_group'])\
            .sum()\
            .reset_index()
        df_age['dimension'] = 'age'
        df_age.rename(columns = {'age_group':'dimension_value'},inplace = True)
        df_age = df_age[['event_date',
                  'dimension',
                  'dimension_value',
                  'views',
                  'likes',
                  'messages_received',
                  'messages_sent',
                  'users_received',
                  'users_sent']]
        return df_age
    
    #объединяем срезы в единую таблицу
    @task
    def transform_merge_result(df_gender,df_os, df_age):
        result = df_gender.append(df_os, ignore_index=True)
        result = result.append(df_age, ignore_index=True)
        print(result.info(5))
        return result

    @task
    def load(result,connection_test):
        ph.to_clickhouse(result,
                 table='a_perepelova_test',
                 index=False,
                 connection=connection_test)
        print(f'Data was added')
        

    feed_info = extract_feeds()
    message_info = extract_messages()
    df = transfrom_merge(feed_info,message_info)
    result = transform_merge_result(transfrom_gender(df),
                                    transfrom_os(df),
                                     transfrom_age(df))
    load(result,connection_test)

slice_table = perepelova_ETL()
