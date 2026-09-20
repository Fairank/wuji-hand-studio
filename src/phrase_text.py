"""Strict ASCII text normalization; no filtering, translation or device I/O."""
import re


def normalize_phrase(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9 \t\r\n]+',value):
        raise ValueError('只支持英文字母、数字和空格 / Use ASCII letters, digits and spaces')
    text=re.sub(r'[ \t\r\n]+',' ',value).strip().upper()
    if not 1<=len(text)<=48:
        raise ValueError('请输入 1–48 个字符 / Enter 1–48 normalized characters')
    return text


def tokens(value):
    return list(normalize_phrase(value))
