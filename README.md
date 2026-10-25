# Место встречи — технический прототип MAX

Бот: https://max.ru/ideya_fiks_103_bot

Проект команды «Рокстар», хакатон «Идея фикс».

**Статус:** подготовлен минимальный код для ручного тестирования. Успешный запуск с реальным токеном и ответ в MAX пока не подтверждены.

## Безопасность

Никогда не отправляйте токен в чат или Codex и не сохраняйте его в Git. Используйте только переменную окружения `MAX_BOT_TOKEN`. Проверка TLS обязательна; не используйте `--insecure` и не отключайте проверку сертификатов.

## Проверка авторизации (PowerShell 7)

Перейдите в корень репозитория после скачивания кода:

```powershell
$env:MAX_BOT_TOKEN = Read-Host "MAX token" -MaskInput
python bot.py
Remove-Item Env:MAX_BOT_TOKEN
```

По умолчанию код выполняет только `GET /me`, без отправки сообщений.

## Тестовое получение /start

Убедитесь, что для бота не настроена подписка на Webhook. Для контролируемого локального теста:

```powershell
$env:MAX_BOT_TOKEN = Read-Host "MAX token" -MaskInput
$env:MAX_BOT_RUN = "1"
try {
    python bot.py
}
finally {
    Remove-Item Env:MAX_BOT_TOKEN -ErrorAction SilentlyContinue
    Remove-Item Env:MAX_BOT_RUN -ErrorAction SilentlyContinue
}
```

Остановите процесс Ctrl+C. Теперь отправьте `/start` в личные сообщения боту. Для получения обновлений используется Long Polling; MAX рекомендует Webhook в рабочей среде.

**Примечание:** PowerShell 7 нужен для маскированного ввода. Docker/Linux имеют отдельные системные хранилища доверенных сертификатов; установка сертификатов в Windows не означает, что они доверены контейнеру.

## Тесты

```bash
python -m unittest discover -s tests -v
```

Тесты выполняются с фиктивными значениями, без внешней сети.

## Docker

```bash
docker build -t place-meeting-bot .
docker run --rm -e MAX_BOT_TOKEN place-meeting-bot
```

Перед запуском контейнера должен быть задан `MAX_BOT_TOKEN` в среде командной оболочки. Для работы с MAX в Linux-контейнере может потребоваться установить доверенный корневой сертификат Минцифры внутри образа; не отключайте TLS. Для постоянной эксплуатации используйте безопасную передачу секретов и Webhook.

## Документация

- https://dev.max.ru/docs-api/methods/GET/me
- https://dev.max.ru/docs-api/methods/GET/updates
- https://dev.max.ru/docs-api/methods/POST/messages
- https://dev.max.ru/docs-api/methods/GET/subscriptions

Разработка ведётся по `AGENTS.md`, с ручной проверкой командой. Согласно п. 9.5 правил хакатона, материалы, полностью или преимущественно созданные генеративным ИИ, запрещены как финальное решение. Перед конкурсным использованием проверьте допустимость подхода с организаторами.
