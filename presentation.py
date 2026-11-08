"""Общее оформление карточек и даты. Данные каталога не изменяем."""
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

ZONE = ZoneInfo('Asia/Barnaul')
MONTHS = 'января февраля марта апреля мая июня июля августа сентября октября ноября декабря'.split()


class Markdown(str):
    # Сохраняем режим при добавлении заголовков и ограничении длины экрана.
    def __add__(self, other): return Markdown(str(self) + str(other))
    def __radd__(self, other): return Markdown(str(other) + str(self))
    def __getitem__(self, key): return Markdown(super().__getitem__(key))


def escape(value):
    return re.sub(r'([\\`*_{}\[\]()#+.!|<>~\-])', r'\\\1', str(value or ''))


def russian_date(value):
    raw=str(value or 'Не указана')
    try:
        parts=re.split(r'\s+[-—–]\s+',raw)
        first=datetime.fromisoformat(parts[0])
        a=first.date()
        if len(parts)==2:
            b=datetime.fromisoformat(parts[1]).date()
            if b<=a:return raw
            left=str(a.day)
            if a.month!=b.month or a.year!=b.year:left+=f' {MONTHS[a.month-1]}'
            if a.year!=b.year:left+=f' {a.year}'
            return f'С {left} по {b.day} {MONTHS[b.month-1]} {b.year}'
        if len(parts)!=1:return raw
        return f'{a.day} {MONTHS[a.month-1]} {a.year}'+(first.strftime(' %H:%M') if len(parts[0])>10 else '')
    except (ValueError,TypeError):return raw


def parse_teacher_date(value, now=None):
    now=now or datetime.now(ZONE)
    parts=re.split(r'\s+-\s+',value.strip())
    if len(parts)>2:raise ValueError('Введите ДД.ММ.ГГ, ДД.ММ.ГГ ЧЧ.ММ или ДД.ММ.ГГ - ДД.ММ.ГГ.')
    parsed=[]
    for part in parts:
        if not re.fullmatch(r'\d{2}\.\d{2}\.\d{2}( \d{2}\.\d{2})?',part):raise ValueError('Нужен формат ДД.ММ.ГГ, время — ЧЧ.ММ.')
        if len(parts)==2 and len(part)>8:raise ValueError('В диапазоне укажите даты без времени.')
        try:
            # Двузначный год всегда относится к 2000–2099.
            expanded=part[:6]+'20'+part[6:]
            date=datetime.strptime(expanded,'%d.%m.%Y %H.%M' if len(part)>8 else '%d.%m.%Y')
        except ValueError:raise ValueError('Такой даты или времени не существует.') from None
        parsed.append(date)
    if parsed[0].date()<now.date() or (len(parts[0])>8 and parsed[0]<now.replace(tzinfo=None)):
        raise ValueError('Дата и время начала не должны быть в прошлом.')
    if len(parsed)==2 and parsed[0]>=parsed[1]:raise ValueError('Дата начала должна быть строго раньше даты окончания.')
    return ' - '.join(d.strftime('%Y-%m-%d %H:%M' if len(parts[i])>8 else '%Y-%m-%d') for i,d in enumerate(parsed))


def event_text(data):
    data=dict(data)
    tags=data.get('tags') or []
    if isinstance(tags,str):
        try:tags=json.loads(tags)
        except ValueError:tags=[tags]
    if not isinstance(tags,list):tags=[]
    # Ограничиваем до экранирования, чтобы не обрезать escape-последовательность.
    return Markdown('# 🎯 '+escape(str(data.get('title') or 'Без названия')[:200])+
        '\n### 📅 Когда: '+escape(russian_date(data.get('date'))[:120])+
        '\n### 📍 Где: '+escape(str(data.get('place') or 'Не указано')[:250])+
        '\n### 🌐 Формат: '+escape(str(data.get('format') or 'Не указан')[:40])+
        '\n\n'+escape(str(data.get('description') or '')[:650])+
        '\n\n*'+escape(', '.join(str(t) for t in tags)[:350])+'*')
