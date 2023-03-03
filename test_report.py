import telegram
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import io
import logging
import pandas as pd
import pandahouse
from read_db.CH import Getch
import os

sns.set()


def test_report(chat=None):
    chat_id = chat or 454623234
    bot = telegram.Bot(token='1987162789:AAFgHNqBv-v5VXPQcS0btoxtXECUvw8akMs')

    msg = 'Hello'
    bot.sendMessage(chat_id=chat_id, text=msg)

    x = np.arange(1, 10, 1)
    y = np.random.choice(5, len(x))
    sns.lineplot(x, y)
    plt.title('test plot')
    plot_object = io.BytesIO()
    plt.savefig(plot_object)
    plot_object.seek(0)
    plot_object.name = 'test_plot.png'
    plt.close()
    bot.sendPhoto(chat_id=chat_id, photo=plot_object)

    data = Getch('select * from simulator.feed_actions where toDate(time) = today() limit 100').df
    file_object = io.StringIO()
    data.to_csv(file_object)
    file_object.name = 'test_file.csv'
    file_object.seek(0)
    bot.sendDocument(chat_id=chat_id, document=file_object)


try:
    test_report()
except Exception as e:
    print(e)