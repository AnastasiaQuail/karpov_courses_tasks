import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import telegram
import pandahouse
from datetime import date
import io
from read_db.CH import Getch
import sys
import os


def check_anomaly(df, metric, threshold=0.3):
    # функция check_anomaly предлагает алгоритм проверки значения на аномальность посредством
    # сравнения интересующего значения со значением в это же время сутки назад
    # при желании алгоритм внутри этой функции можно изменить
    current_ts = df['ts'].max()  # достаем максимальную 15-минутку из датафрейма - ту, которую будем проверять на аномальность
    day_ago_ts = current_ts - pd.DateOffset(days=1)  # достаем такую же 15-минутку сутки назад

    current_value = df[df['ts'] == current_ts][metric].iloc[0] # достаем из датафрейма значение метрики в максимальную 15-минутку
    day_ago_value = df[df['ts'] == day_ago_ts][metric].iloc[0] # достаем из датафрейма значение метрики в такую же 15-минутку сутки назад

    # вычисляем отклонение
    if current_value <= day_ago_value:
        diff = abs(current_value / day_ago_value - 1)
    else:
        diff = abs(day_ago_value / current_value - 1)

    # проверяем больше ли отклонение метрики заданного порога threshold
    # если отклонение больше, то вернем 1, в противном случае 0
    if diff > threshold:
        is_alert = 1
    else:
        is_alert = 0

    return is_alert, current_value, diff


def run_alerts(chat=None):
    chat_id = chat or 454623234
    bot = telegram.Bot(token='1987162789:AAFgHNqBv-v5VXPQcS0btoxtXECUvw8akMs')

    # для удобства построения графиков в запрос можно добавить колонки date, hm
    data = Getch(''' SELECT
                          toStartOfFifteenMinutes(time) as ts
                        , toDate(ts) as date
                        , formatDateTime(ts, '%R') as hm
                        , uniqExact(user_id) as users_lenta
                    FROM simulator.feed_actions
                    WHERE ts >=  today() - 1 and ts < toStartOfFifteenMinutes(now())
                    GROUP BY ts, date, hm
                    ORDER BY ts ''').df

    metric = 'users_lenta'
    is_alert, current_value, diff = check_anomaly(data, metric, threshold=0.1) # проверяем метрику на аномальность алгоритмом, описаным внутри функции check_anomaly()
    if is_alert:
        msg = '''Метрика {metric}:\nтекущее значение = {current_value:.2f}\nотклонение от вчера {diff:.2%}'''.format(metric=metric,
                                                                                                                     current_value=current_value,
                                                                                                                     diff=diff)

        sns.set(rc={'figure.figsize': (16, 10)}) # задаем размер графика
        plt.tight_layout()

        ax = sns.lineplot( # строим линейный график
            data=data.sort_values(by=['date', 'hm']), # задаем датафрейм для графика
            x="hm", y=metric, # указываем названия колонок в датафрейме для x и y
            hue="date" # задаем "группировку" на графике, т е хотим чтобы для каждого значения date была своя линия построена
            )

        for ind, label in enumerate(ax.get_xticklabels()): # этот цикл нужен чтобы разрядить подписи координат по оси Х,
            if ind % 15 == 0:
                label.set_visible(True)
            else:
                label.set_visible(False)
        ax.set(xlabel='time') # задаем имя оси Х
        ax.set(ylabel=metric) # задаем имя оси У

        ax.set_title('{}'.format(metric)) # задае заголовок графика
        ax.set(ylim=(0, None)) # задаем лимит для оси У

        # формируем файловый объект
        plot_object = io.BytesIO()
        ax.figure.savefig(plot_object)
        plot_object.seek(0)
        plot_object.name = '{0}.png'.format(metric)
        plt.close()

        # отправляем алерт
        bot.sendMessage(chat_id=chat_id, text=msg)
        bot.sendPhoto(chat_id=chat_id, photo=plot_object)


try:
    run_alerts()
except Exception as e:
    print(e)