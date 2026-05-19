import hashlib
import json
import os
import random
import time
from urllib.parse import urlencode

import requests

from logger import logger
from notifier import get_notifier

# 随机配置
PHONE_BRAND_TYPE_LIST = ["MI", "Huawei", "UN", "OPPO", "VO"]

# 公共配置
PLATFORM = '2'
GKEY = '000000'
PHONE_BRAND_TYPE = random.choice(PHONE_BRAND_TYPE_LIST)
USER_AGENT = "okhttp/3.8.1"
SIGN_SALT = 'cPqzc91RXPJlNiWSPrDpzJjo2YuiImtx'

# 已验证的新 profile
TOOL_PROFILE = {
    "market_id": "tool_web",
    "app_version": "4.4.1.6",
    "versioncode": "496",
    "device_code": "[d]6c7aa17e-d2eb-4008-9bef-cab11dc642e7",
}

FLOOR_PROFILE = {
    "market_id": "floor_huluxia",
    "app_version": "4.4.0.6",
    "versioncode": "20141528",
    "device_code": "[d]8a55f6af-134d-4ea9-a915-f5e1a2f2f037",
}

DEFAULT_PROFILE = FLOOR_PROFILE
SIGNIN_PROFILE = TOOL_PROFILE

headers = {
    "Connection": "close",
    "Accept-Encoding": "gzip",
    "User-Agent": USER_AGENT,
}
session = requests.Session()


def load_fallback_categories():
    with open('cat_id.json', 'r', encoding='UTF-8') as f:
        return json.load(f)


fallback_cat_id_dict = load_fallback_categories()


class HuluxiaSignin:
    """
    葫芦侠三楼签到类
    """

    def __init__(self):
        self._key = ''
        self.cat_id = ''
        self.userid = ''
        self.signin_continue_days = ''
        self.cat_id_dict = {}

        notifier_type = os.getenv("NOTIFIER_TYPE", "none")
        config = {
            "webhook_url": os.getenv("WECHAT_ROBOT_URL"),
            "smtp_server": "smtp.qq.com",
            "port": 465
        }
        if notifier_type == "email":
            email_config_str = os.getenv("EMAIL_CONFIG")
            if email_config_str:
                try:
                    email_config = json.loads(email_config_str)
                    config.update({
                        "username": email_config.get("username"),
                        "auth_code_or_password": email_config.get("auth_code_or_password"),
                        "sender_email": email_config.get("sender_email"),
                        "recipient_email": email_config.get("recipient_email")
                    })
                except json.JSONDecodeError:
                    print("邮箱配置格式错误，请检查 EMAIL_CONFIG 的值。")
                    raise
            else:
                print("没有配置 EMAIL_CONFIG 环境变量，请设置邮箱相关配置。")
                raise ValueError("缺少邮箱配置")
        self.notifier = get_notifier(notifier_type, config)

    @staticmethod
    def md5(text: str) -> str:
        md5_obj = hashlib.md5()
        md5_obj.update(text.encode("utf-8"))
        return md5_obj.hexdigest()

    @staticmethod
    def timestamp_millis() -> int:
        return int(time.time() * 1000)

    @staticmethod
    def safe_text(value) -> str:
        return "" if value is None else str(value).strip()

    def common_params(self, key='', profile=None):
        current_profile = profile or DEFAULT_PROFILE
        return {
            "platform": PLATFORM,
            "gkey": GKEY,
            "app_version": current_profile["app_version"],
            "versioncode": current_profile["versioncode"],
            "market_id": current_profile["market_id"],
            "_key": self.safe_text(key),
            "device_code": current_profile["device_code"],
            "phone_brand_type": PHONE_BRAND_TYPE,
        }

    def build_url(self, base_url, params):
        return f"{base_url}?{urlencode(params)}"

    def request_json(self, method, url, *, params=None, data=None, timeout=20):
        response = session.request(
            method=method,
            url=url,
            params=params,
            data=data,
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()

    def login_sign(self, account, password_md5, profile):
        sign_source = (
            "account" + self.safe_text(account)
            + "device_code" + profile["device_code"]
            + "password" + self.safe_text(password_md5)
            + "voice_code" + SIGN_SALT
        )
        return self.md5(sign_source)

    def signin_sign(self, category_id, timestamp):
        sign_source = f"cat_id{category_id}time{timestamp}{SIGN_SALT}"
        return self.md5(sign_source)

    def psd_login(self, account, password):
        profile = DEFAULT_PROFILE
        login_url = 'http://floor.huluxia.com/account/login/ANDROID/4.2.6'
        password_md5 = self.md5(password)
        params = self.common_params("", profile)
        post_data = {
            "account": self.safe_text(account),
            "login_type": "2",
            "password": password_md5,
            "sign": self.login_sign(account, password_md5, profile),
        }
        return self.request_json("POST", login_url, params=params, data=post_data)

    def set_config(self, acc, psd):
        data = self.psd_login(acc, psd)
        status = data.get('status')
        if status != 1 or not data.get('_key') or not data.get('user'):
            message = data.get('msg', '手机号或密码错误!')
            self.notifier.send(message)
            raise ValueError(message)
        self._key = data['_key']
        self.userid = str(data['user'].get('userID', '')).strip()
        if not self.userid:
            raise ValueError("登录成功但未返回 userID")
        return self._key

    def user_info(self):
        info_url = 'http://floor.huluxia.com/user/info/ANDROID/4.1.8'
        params = self.common_params(self._key, DEFAULT_PROFILE)
        params["user_id"] = str(self.userid)
        data = self.request_json("GET", info_url, params=params)
        nick = data['nick']
        level = data['level']
        exp = data['exp']
        next_exp = data['nextExp']
        return nick, level, exp, next_exp

    def fetch_categories(self):
        """
        动态获取当前可签到板块列表。

        优先使用实时接口；失败时回退到本地 cat_id.json。
        """
        category_url = 'http://floor.huluxia.com/category/list/ANDROID/4.2.3'
        params = self.common_params(self._key, DEFAULT_PROFILE)
        params["is_hidden"] = "1"
        params["is_recommended"] = "1"
        try:
            category_res = self.request_json("GET", category_url, params=params)
            categories = category_res.get('categories', [])
            category_map = {}
            for item in categories:
                category_id = self.safe_text(item.get('categoryID'))
                title = self.safe_text(item.get('title'))
                if not category_id:
                    continue
                category_map[category_id] = title or f'版块{category_id}'
            if category_map:
                logger.info(f"动态获取到 {len(category_map)} 个板块")
                self.cat_id_dict = category_map
                return category_map
            logger.warning("动态板块接口返回为空，回退本地 cat_id.json")
        except Exception as e:
            logger.warning(f"动态获取板块失败，回退本地 cat_id.json：{e}")
        self.cat_id_dict = dict(fallback_cat_id_dict)
        return self.cat_id_dict

    def huluxia_signin(self, acc, psd):
        self.set_config(acc, psd)
        info = self.user_info()
        initial_msg = f'正在为{info[0]}签到\n等级：Lv.{info[1]}\n经验值：{info[2]}/{info[3]}'
        logger.info(initial_msg)

        notifier_type = os.getenv("NOTIFIER_TYPE")
        print("通知类型：", notifier_type)

        if notifier_type == "wechat":
            self.notifier.send(initial_msg)
            all_messages = []
        elif notifier_type == "email":
            all_messages = [initial_msg]
        else:
            all_messages = []

        total_exp = 0
        category_map = self.fetch_categories()
        signin_url = "http://floor.huluxia.com/user/signin/ANDROID/4.2.6"

        for ct in category_map.keys():
            self.cat_id = ct
            timestamp = self.timestamp_millis()
            sign = self.signin_sign(self.cat_id, timestamp)
            params = self.common_params(self._key, SIGNIN_PROFILE)
            params["cat_id"] = self.cat_id
            params["time"] = str(timestamp)
            post_data = {"sign": sign}
            try:
                signin_res = self.request_json("POST", signin_url, params=params, data=post_data)
            except Exception as e:
                error_msg = f"签到过程中出现错误：{e}"
                if notifier_type == "wechat":
                    self.notifier.send(error_msg)
                elif notifier_type == "email":
                    all_messages.append(error_msg)
                logger.error(error_msg)
                break

            if signin_res.get('status') == 0:
                fail_msg = f'【{category_map.get(self.cat_id, self.cat_id)}】签到失败，请手动签到。'
                if notifier_type == "wechat":
                    self.notifier.send(fail_msg)
                elif notifier_type == "email":
                    all_messages.append(fail_msg)
                logger.warning(fail_msg)
                time.sleep(3)
                continue

            signin_exp = signin_res.get('experienceVal', 0)
            self.signin_continue_days = signin_res.get('continueDays', 0)
            success_msg = f'【{category_map.get(self.cat_id, self.cat_id)}】签到成功，经验值 +{signin_exp}'
            if notifier_type == "wechat":
                self.notifier.send(success_msg)
            elif notifier_type == "email":
                all_messages.append(success_msg)
            logger.info(success_msg)
            total_exp += signin_exp
            time.sleep(3)

        summary_msg = f'本次为{info[0]}签到共获得：{total_exp} 经验值'
        if notifier_type == "wechat":
            self.notifier.send(summary_msg)
        elif notifier_type == "email":
            all_messages.append(summary_msg)
        logger.info(summary_msg)

        final_info = self.user_info()
        final_msg = (
            f'已为{final_info[0]}完成签到\n'
            f'等级：Lv.{final_info[1]}\n'
            f'经验值：{final_info[2]}/{final_info[3]}\n'
            f'已连续签到 {self.signin_continue_days} 天\n'
        )
        remaining_days = (int(final_info[3]) - int(final_info[2])) // total_exp + 1 if total_exp else "未知"
        final_msg += f'还需签到 {remaining_days} 天'
        if notifier_type == "wechat":
            self.notifier.send(final_msg)
        elif notifier_type == "email":
            all_messages.append(final_msg)
        logger.info(final_msg)

        if notifier_type == "email" and all_messages:
            self.notifier.send("\n\n".join(all_messages))
