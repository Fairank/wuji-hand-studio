"""Reviewed subset of local Claude Fable 5.1 max input-regression suggestions.

Main maintainer adapted assertions to this application's ValueError contract;
Unicode exhaustive sweeps and message-format assertions were not adopted.
"""
import string
import unittest
from phrase_text import normalize_phrase,tokens


class PhraseTextTests(unittest.TestCase):
    def test_wuji_tech_keeps_every_letter(self):
        for raw in ('wuji tech','WUJI TECH','Wuji Tech','  wuji \t tech\r\n'):
            self.assertEqual(normalize_phrase(raw),'WUJI TECH')
            self.assertEqual(tokens(raw),['W','U','J','I',' ','T','E','C','H'])

    def test_letters_digits_and_jz(self):
        self.assertEqual(normalize_phrase(string.ascii_lowercase),string.ascii_uppercase)
        for raw,expected in [('jz','JZ'),('jazz','JAZZ'),('Jazz Quiz 2','JAZZ QUIZ 2'),('007','007')]:
            self.assertEqual(normalize_phrase(raw),expected)

    def test_trim_collapse_and_empty(self):
        for raw,expected in [('a\tb','A B'),('a\r\nb','A B'),('a \t\r\n b','A B'),('a'+' '*100+'b','A B')]:
            self.assertEqual(normalize_phrase(raw),expected)
        for raw in ('',' ','\t','\r\n'):
            with self.assertRaises(ValueError):normalize_phrase(raw)

    def test_48_49_boundary_includes_spaces(self):
        for raw in ('a'*48,'a'*24+' '+'a'*23,' \t'+'a'*48+'\r\n'):
            self.assertEqual(len(normalize_phrase(raw)),48)
        for raw in ('a'*49,'a'*24+' '+'a'*24):
            with self.assertRaises(ValueError):normalize_phrase(raw)

    def test_unicode_cannot_silently_become_ascii(self):
        for raw in ('stra\u00dfe','wuj\u0131 tech','te\u017ft','\ufb01ve','\uff57\uff55\uff4a\uff49','\u212a9','A\u00a0B','4\u06638','\U0001f600'):
            with self.subTest(raw=ascii(raw)):
                with self.assertRaises(ValueError):normalize_phrase(raw)

    def test_non_text_and_punctuation_rejected(self):
        for raw in (None,48,True,b'wuji',['wuji'],{'wuji':1},'WUJI!','A/B','a\x00b','a\x0bb'):
            with self.assertRaises(ValueError):normalize_phrase(raw)


if __name__=='__main__':unittest.main()
