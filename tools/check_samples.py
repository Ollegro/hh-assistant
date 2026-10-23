# -*- coding: utf-8 -*-
"""Диагностика: находит ли приложение образцы писем для few-shot генерации.

Запуск: python tools/check_samples.py
"""
import os
import sys

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

from hhassist import letters  # noqa: E402

samples = letters.load_samples()
print("папка приложения:", APP)
print("шаблон поиска:   ", letters.SAMPLES_GLOB)
print("найдено образцов:", len(samples))
if samples:
    print("первый образец:  ", len(samples[0]), "знаков")
