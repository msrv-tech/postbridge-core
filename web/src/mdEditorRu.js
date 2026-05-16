/**
 * Русские подписи тулбара и aria-label для @uiw/react-md-editor.
 * Через commandsFilter, без правок node_modules.
 */

const MD_EDITOR_RU_BY_NAME = {
  bold: { title: 'Жирный (Ctrl+B)', ariaLabel: 'Жирный (Ctrl+B)' },
  italic: { title: 'Курсив (Ctrl+I)', ariaLabel: 'Курсив (Ctrl+I)' },
  strikethrough: {
    title: 'Зачёркнутый (Ctrl+Shift+X)',
    ariaLabel: 'Зачёркнутый (Ctrl+Shift+X)',
  },
  hr: { title: 'Разделитель (Ctrl+H)', ariaLabel: 'Горизонтальная линия (Ctrl+H)' },
  title: { title: 'Заголовок', ariaLabel: 'Вставить заголовок' },
  heading1: { title: 'Заголовок 1 (Ctrl+1)', ariaLabel: 'Заголовок 1 (Ctrl+1)' },
  heading2: { title: 'Заголовок 2 (Ctrl+2)', ariaLabel: 'Заголовок 2 (Ctrl+2)' },
  heading3: { title: 'Заголовок 3 (Ctrl+3)', ariaLabel: 'Заголовок 3 (Ctrl+3)' },
  heading4: { title: 'Заголовок 4 (Ctrl+4)', ariaLabel: 'Заголовок 4 (Ctrl+4)' },
  heading5: { title: 'Заголовок 5 (Ctrl+5)', ariaLabel: 'Заголовок 5 (Ctrl+5)' },
  heading6: { title: 'Заголовок 6 (Ctrl+6)', ariaLabel: 'Заголовок 6 (Ctrl+6)' },
  link: { title: 'Ссылка (Ctrl+L)', ariaLabel: 'Вставить ссылку (Ctrl+L)' },
  quote: { title: 'Цитата (Ctrl+Q)', ariaLabel: 'Вставить цитату (Ctrl+Q)' },
  code: { title: 'Код (Ctrl+J)', ariaLabel: 'Вставить код (Ctrl+J)' },
  codeBlock: {
    title: 'Блок кода (Ctrl+Shift+J)',
    ariaLabel: 'Вставить блок кода (Ctrl+Shift+J)',
  },
  comment: { title: 'Комментарий (Ctrl+/)', ariaLabel: 'Вставить комментарий (Ctrl+/)' },
  image: { title: 'Изображение (Ctrl+K)', ariaLabel: 'Вставить изображение (Ctrl+K)' },
  table: { title: 'Таблица', ariaLabel: 'Вставить таблицу' },
  'unordered-list': {
    title: 'Маркированный список (Ctrl+Shift+U)',
    ariaLabel: 'Маркированный список (Ctrl+Shift+U)',
  },
  'ordered-list': {
    title: 'Нумерованный список (Ctrl+Shift+O)',
    ariaLabel: 'Нумерованный список (Ctrl+Shift+O)',
  },
  'checked-list': {
    title: 'Список с чекбоксами (Ctrl+Shift+C)',
    ariaLabel: 'Список с чекбоксами (Ctrl+Shift+C)',
  },
  help: { title: 'Справка', ariaLabel: 'Открыть справку' },
  preview: {
    title: 'Только превью (Ctrl+9)',
    ariaLabel: 'Только превью (Ctrl+9)',
  },
  edit: {
    title: 'Только код (Ctrl+7)',
    ariaLabel: 'Только исходный код (Ctrl+7)',
  },
  live: {
    title: 'Код и превью (Ctrl+8)',
    ariaLabel: 'Код и превью одновременно (Ctrl+8)',
  },
  fullscreen: {
    title: 'На весь экран (Ctrl+0)',
    ariaLabel: 'Переключить полноэкранный режим (Ctrl+0)',
  },
}

function applyRuLabels(command) {
  const ru = command.name ? MD_EDITOR_RU_BY_NAME[command.name] : undefined
  if (!ru || !command.buttonProps) return command
  return {
    ...command,
    buttonProps: {
      ...command.buttonProps,
      title: ru.title,
      'aria-label': ru.ariaLabel,
    },
  }
}

const HELP_MARKDOWN_RU =
  'https://docs.github.com/ru/get-started/writing-on-github/getting-started-with-writing-and-formatting-on-github/basic-writing-and-formatting-syntax'

/**
 * Передавать в MDEditor: commandsFilter={mdEditorCommandsFilter}
 */
export function mdEditorCommandsFilter(command, locale = 'ru') {
  if (!command) return command
  if (String(locale || 'ru').toLowerCase().startsWith('en')) return command
  let next = command
  if (Array.isArray(command.children)) {
    const mapped = command.children.map((child) => mdEditorCommandsFilter(child, locale))
    if (mapped.some((c, i) => c !== command.children[i])) {
      next = { ...command, children: mapped }
    }
  }
  let out = applyRuLabels(next)
  if (out.name === 'help') {
    out = {
      ...out,
      execute: () => {
        window.open(HELP_MARKDOWN_RU, '_blank', 'noreferrer')
      },
    }
  }
  return out
}
