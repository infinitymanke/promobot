#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@Description: xbogus.py
Запасная подпись TikTok (X-Bogus) — чистый Python порт из Johnserf-Seed/f2 (Apache 2.0).
Используется как резерв, если X-Gnarly перестанет приниматься после обновления SDK.
"""

import base64
import hashlib
import time


class XBogus:
    def __init__(self, user_agent: str = "") -> None:
        self.character = "Dkdpgh4ZKsQB80/Mfvw36XI1R25-WUAlEi7NLboqYTOPuzmFjJnryx9HVGcaStCe="
        self.ua_key = b"\x00\x01\x0c"
        self.user_agent = (
            user_agent
            if user_agent is not None and user_agent != ""
            else "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )

    def str_to_array(self, text):
        return [ord(char) for char in text]

    def md5_str_to_array(self, md5_str):
        if isinstance(md5_str, str) and len(md5_str) > 32:
            return self.str_to_array(md5_str)
        return list(bytes.fromhex(md5_str))

    def md5(self, input_data):
        if isinstance(input_data, str):
            array = self.str_to_array(input_data)
        elif isinstance(input_data, list):
            array = input_data
        else:
            raise ValueError("Invalid input type. Expected str or list.")

        md5_hash = hashlib.md5()
        md5_hash.update(bytes(array))
        return md5_hash.hexdigest()

    def md5_encrypt(self, url_params):
        hashed = self.md5_str_to_array(
            self.md5(self.md5_str_to_array(self.md5(url_params)))
        )
        return hashed

    def encoding_conversion(self, a, b, c, e, d, t, f, r, n, o, i, _, x, u, s, l, v, h, p):
        y = [a]
        y.append(int(i))
        y.extend([b, _, c, x, e, u, d, s, t, l, f, v, r, h, n, p, o])
        re = bytes(y).decode("ISO-8859-1")
        return re

    def encoding_conversion2(self, a, b, c):
        return chr(a) + chr(b) + c

    def rc4_encrypt(self, key, data):
        S = list(range(256))
        j = 0
        encrypted_data = bytearray()

        for i in range(256):
            j = (j + S[i] + key[i % len(key)]) % 256
            S[i], S[j] = S[j], S[i]

        i = j = 0
        for byte in data:
            i = (i + 1) % 256
            j = (j + S[i]) % 256
            S[i], S[j] = S[j], S[i]
            encrypted_byte = byte ^ S[(S[i] + S[j]) % 256]
            encrypted_data.append(encrypted_byte)

        return encrypted_data

    def calculation(self, a1, a2, a3):
        x1 = (a1 & 255) << 16
        x2 = (a2 & 255) << 8
        x3 = x1 | x2 | a3
        return (
            self.character[(x3 & 16515072) >> 18]
            + self.character[(x3 & 258048) >> 12]
            + self.character[(x3 & 4032) >> 6]
            + self.character[x3 & 63]
        )

    def getXBogus(self, url_params: str, body: str = ""):
        array1 = self.md5_str_to_array(
            self.md5(
                base64.b64encode(
                    self.rc4_encrypt(self.ua_key, self.user_agent.encode("ISO-8859-1"))
                ).decode("ISO-8859-1")
            )
        )
        array2 = self.md5_encrypt(body)
        url_params_array = self.md5_encrypt(url_params)

        timer = int(time.time())
        ct = 536919696
        array3 = []
        array4 = []
        xb_ = ""
        new_array = [
            64,
            0.00390625,
            1,
            12,
            url_params_array[14],
            url_params_array[15],
            array2[14],
            array2[15],
            array1[14],
            array1[15],
            timer >> 24 & 255,
            timer >> 16 & 255,
            timer >> 8 & 255,
            timer & 255,
            ct >> 24 & 255,
            ct >> 16 & 255,
            ct >> 8 & 255,
            ct & 255,
        ]
        xor_result = new_array[0]
        for i in range(1, len(new_array)):
            b = new_array[i]
            if isinstance(b, float):
                b = int(b)
            xor_result ^= b

        new_array.append(xor_result)

        idx = 0
        while idx < len(new_array):
            array3.append(new_array[idx])
            try:
                array4.append(new_array[idx + 1])
            except IndexError:
                pass
            idx += 2

        merge_array = array3 + array4

        garbled_code = self.encoding_conversion2(
            2,
            255,
            self.rc4_encrypt(
                "\xff".encode("ISO-8859-1"),
                self.encoding_conversion(*merge_array).encode("ISO-8859-1"),
            ).decode("ISO-8859-1"),
        )

        idx = 0
        while idx < len(garbled_code):
            xb_ += self.calculation(
                ord(garbled_code[idx]),
                ord(garbled_code[idx + 1]),
                ord(garbled_code[idx + 2]),
            )
            idx += 3
        self.params = "%s&X-Bogus=%s" % (url_params, xb_)
        self.xb = xb_
        return (self.params, self.xb, self.user_agent)


if __name__ == "__main__":
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    XB = XBogus(user_agent=ua)
    tk_url_params = "aid=1988&app_language=zh-Hans&app_name=tiktok_web&channel=tiktok_web&cookie_enabled=true&device_platform=web_pc&os=windows&region=SG"
    tk_xbogus = XB.getXBogus(tk_url_params)
    print(f"xbogus:{tk_xbogus[1]}, ua: {tk_xbogus[2]}")