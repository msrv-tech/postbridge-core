from postbridge.i18n import get_i18n


def test_translate_uses_requested_locale():
    i18n = get_i18n()

    result = i18n.translate(
        "bot.channel.connected",
        locale="ru",
        params={"title": "Новости"},
    )

    assert result == 'Канал "Новости" подключён.\n\nПродолжите в веб-приложении, чтобы завершить настройку, миграцию или AI-сценарии.'


def test_translate_falls_back_to_english_for_unknown_locale():
    i18n = get_i18n()

    result = i18n.translate(
        "bot.button.open_web",
        locale="de",
    )

    assert result == "Open web"


def test_resolve_locale_prefers_supported_primary_subtag():
    i18n = get_i18n()

    resolved = i18n.resolve_locale(platform_locale="ru-RU")

    assert resolved.locale == "ru"
    assert resolved.source == "platform"


def test_resolve_locale_uses_accept_language_before_default():
    i18n = get_i18n()

    resolved = i18n.resolve_locale(accept_language="de-DE,de;q=0.8,ru;q=0.7,en;q=0.6")

    assert resolved.locale == "ru"
    assert resolved.source == "accept_language"
