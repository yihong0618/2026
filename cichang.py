import argparse
import hashlib
import json
from base64 import b64decode
import os
from random import shuffle, choice
import time

import requests
import telebot
import pendulum
from openai import OpenAI
from rich import print
from telegramify_markdown import markdownify
from wcwidth import wcswidth


if api_base := os.environ.get("OPENAI_API_BASE"):
    client = OpenAI(base_url=api_base, api_key=os.environ.get("OPENAI_API_KEY"))
else:
    client = OpenAI()

TIMEZONE = "Asia/Shanghai"
KEY = "NDVmZDE3ZTAyMDAzZDg5YmVlN2YwNDZiYjQ5NGRlMTM="
LOGIN_URL = "https://pass.hujiang.com/Handler/UCenter.json?action=Login&isapp=true&language=zh_CN&password={password}&timezone=8&user_domain=hj&username={user_name}"
COVERT_URL = "https://pass-cdn.hjapi.com/v1.1/access_token/convert"

# added in 2023.06.08
XIAOD_LIST_URL = "https://vocablist.hjapi.com/notebook/notebooklist?lastSyncDate=2000-01-01T00%3A00%3A00.000&lastSyncVer=0&syncVer=10&sortField=TIME&sortord=DESC"
XIAOD_ONE_NOTE_URL = "https://dict.hujiang.com/notebookweb/notewords?lastSyncDate=2000-01-01T00%3A00%3A00.000&nbookid={nbook_id}&pageNo=1&pageSize=1000&sortField=TIME&sortord=DESC"


def md5_encode(string):
    m = hashlib.md5()
    m.update(string.encode())
    return m.hexdigest()


####### XIAOD #######
def get_xiaod_notes_dict(s):
    r = s.get(XIAOD_LIST_URL)
    if not r.ok:
        raise Exception("Can not note books info from hujiang")
    d = {}
    node_list = r.json()["data"]["noteList"]
    for n in node_list:
        d[n["nbookId"]] = n["nbookName"]
    return d


def get_xiaod_words(s, nbook_id):
    r = s.get(XIAOD_ONE_NOTE_URL.format(nbook_id=nbook_id))
    if not r.ok:
        raise Exception(f"Can not get words for nbook_id: {nbook_id}")
    return r.json()


def login(user_name, password):
    s = requests.Session()
    password_md5 = md5_encode(password)
    r = s.get(LOGIN_URL.format(user_name=user_name, password=password_md5))
    if not r.ok:
        raise Exception(f"Someting is wrong to login -- {r.text}")
    club_auth_cookie = r.json()["Data"]["Cookie"]
    data = {"club_auth_cookie": club_auth_cookie}
    HJKEY = b64decode(KEY).decode()
    headers = {"hj_appkey": HJKEY, "Content-Type": "application/json"}
    # real login to get real token
    r = s.post(COVERT_URL, headers=headers, data=json.dumps(data))
    if not r.ok:
        raise Exception(f"Get real token failed -- {r.text}")
    access_token = r.json()["data"]["access_token"]
    headers["Access-Token"] = access_token
    s.headers = headers
    return s


def learning_curve_days():
    now = pendulum.now(TIMEZONE)
    days_list = [2, 3, 5, 8, 16, 31]
    return [now.subtract(days=d).to_date_string() for d in days_list]


def make_xiaod_note_words(s):
    words_dict = {}
    now = pendulum.now(TIMEZONE)
    note_dict = get_xiaod_notes_dict(s)
    new_words = []
    new_words_define = []
    symbol_list = []
    curve_days_words = []
    curve_days_define = []
    curve_days_symbol_list = []
    for k, v in note_dict.items():
        data = get_xiaod_words(s, k)
        word_list = data["data"]["wordList"]
        if not word_list:
            continue
        for word in word_list:
            add_date = word["clientDateUpdated"]
            add_date = pendulum.parse(add_date)
            if add_date.to_date_string() in {
                now.to_date_string(),
                now.subtract(days=1).to_date_string(),
            }:
                new_words.append(word["word"])
                new_words_define.append(word["definition"])
                symbol_list.append(word["symbol1"])
            if add_date.to_date_string() in learning_curve_days():
                curve_days_words.append(word["word"])
                curve_days_define.append(word["definition"])
                curve_days_symbol_list.append(word["symbol1"])
    if new_words:
        words_dict["new_words"] = {
            "words": new_words,
            "define": new_words_define,
            "symbol": symbol_list,
        }
    if curve_days_words:
        words_dict["curve_days_words"] = {
            "words": curve_days_words,
            "define": curve_days_define,
            "symbol": curve_days_symbol_list,
        }
    return words_dict


def send_word_messages(bot, chat_id, title, word_list, define_list, symbol_list):
    def str_width(s):
        return wcswidth(s)

    def pad_text(text, target_width):
        current = str_width(text)
        diff = target_width - current
        pad = ""
        if diff > 0:
            if diff % 2 == 1:
                pad += " "
                diff -= 1
            pad += "\u3000" * (diff // 2)
        return text + pad

    def pad_index(index, max_width):
        return str(index).rjust(max_width)

    max_word_width = max([str_width(w) for w in word_list])
    max_index_width = len(str(len(word_list)))
    combined_list = []

    for i, (word, symbol) in enumerate(zip(word_list, symbol_list)):
        padded_index = pad_index(i + 1, max_index_width)
        padded_word = pad_text(word, max_word_width)
        combined_list.append(f"{padded_index}\\. `{padded_word}｜` ||{symbol}||")

    bot.send_message(
        chat_id,
        markdownify(title + "\n" + "\n".join(combined_list)),
        parse_mode="MarkdownV2",
    )

    numbered_defines = [f"{i+1}\\. {define}" for i, define in enumerate(define_list)]
    bot.send_message(
        chat_id,
        markdownify("Definition:\n" + "\n".join(numbered_defines)),
        parse_mode="MarkdownV2",
    )


def main(user_name, password, token, tele_token, tele_chat_id):
    try:
        s = requests.Session()
        HJKEY = b64decode(KEY).decode()
        headers = {"hj_appkey": HJKEY, "Content-Type": "application/json"}
        s.headers = headers
        headers["Access-Token"] = token
        words_dict = make_xiaod_note_words(s)
    except Exception:
        s = login(user_name, password)
        words_dict = make_xiaod_note_words(s)
    if not words_dict:
        return
    bot = telebot.TeleBot(tele_token)
    if today_words := words_dict.get("new_words"):
        word_list = today_words["words"]
        # first send words
        send_word_messages(
            bot,
            tele_chat_id,
            "Today's words with pronunciation:",
            word_list,
            today_words["define"],
            today_words["symbol"],
        )
        # second send mp3
        shuffle(word_list)
        make_story_prompt = "Make a story using these words the story should be written in Japanese words: `{}`"
        words = ",".join(word_list)
        prompt = make_story_prompt.format(words)
        try:
            completion = client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model="gpt-4o-2024-05-13",
            )
            head = "Words: " + words + "\n"
            story = completion.choices[0].message.content.encode("utf8").decode()
            audio = client.audio.speech.create(
                model="tts-1",
                voice=choice(["alloy", "echo", "fable", "onyx", "nova", "shimmer"]),
                input=story,
            )
            print("Audio created")
            # make all word in words to be bold
            for word in word_list:
                story = story.replace(word, f"`{word}`")
            # create mp3 file with date in name
            speech_file_path = f"words_{pendulum.now().to_date_string()}.mp3"
            audio.write_to_file(speech_file_path)
            content = head + story
            bot.send_audio(
                tele_chat_id,
                open(speech_file_path, "rb"),
                caption=markdownify(content),
                parse_mode="MarkdownV2",
            )
            # cleanup file after sending
            os.remove(speech_file_path)
            # spider rule
            time.sleep(1)
        except Exception as e:
            print(str(e))
            print("Can not make story")

    # send learning curve words last
    if curve_days_words := words_dict.get("curve_days_words"):
        send_word_messages(
            bot,
            tele_chat_id,
            "Learning curve words with pronunciation:",
            curve_days_words["words"],
            curve_days_words["define"],
            curve_days_words["symbol"],
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("user_name", help="hujiang_user_name")
    parser.add_argument("password", help="hujiang_password")
    parser.add_argument("token", help="token", default=None, nargs="?")
    parser.add_argument(
        "--tele_token", help="tele_token", nargs="?", default="", const=""
    )
    parser.add_argument(
        "--tele_chat_id", help="tele_chat_id", nargs="?", default="", const=""
    )
    options = parser.parse_args()
    main(
        options.user_name,
        options.password,
        options.token,
        options.tele_token,
        options.tele_chat_id,
    )
