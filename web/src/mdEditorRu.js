const MD_EDITOR_LABELS_BY_NAME = {
  bold: ['markdownEditor.bold.title', 'markdownEditor.bold.aria'],
  italic: ['markdownEditor.italic.title', 'markdownEditor.italic.aria'],
  strikethrough: ['markdownEditor.strikethrough.title', 'markdownEditor.strikethrough.aria'],
  hr: ['markdownEditor.hr.title', 'markdownEditor.hr.aria'],
  title: ['markdownEditor.title.title', 'markdownEditor.title.aria'],
  heading1: ['markdownEditor.heading1.title', 'markdownEditor.heading1.aria'],
  heading2: ['markdownEditor.heading2.title', 'markdownEditor.heading2.aria'],
  heading3: ['markdownEditor.heading3.title', 'markdownEditor.heading3.aria'],
  heading4: ['markdownEditor.heading4.title', 'markdownEditor.heading4.aria'],
  heading5: ['markdownEditor.heading5.title', 'markdownEditor.heading5.aria'],
  heading6: ['markdownEditor.heading6.title', 'markdownEditor.heading6.aria'],
  link: ['markdownEditor.link.title', 'markdownEditor.link.aria'],
  quote: ['markdownEditor.quote.title', 'markdownEditor.quote.aria'],
  code: ['markdownEditor.code.title', 'markdownEditor.code.aria'],
  codeBlock: ['markdownEditor.codeBlock.title', 'markdownEditor.codeBlock.aria'],
  comment: ['markdownEditor.comment.title', 'markdownEditor.comment.aria'],
  image: ['markdownEditor.image.title', 'markdownEditor.image.aria'],
  table: ['markdownEditor.table.title', 'markdownEditor.table.aria'],
  'unordered-list': ['markdownEditor.unorderedList.title', 'markdownEditor.unorderedList.aria'],
  'ordered-list': ['markdownEditor.orderedList.title', 'markdownEditor.orderedList.aria'],
  'checked-list': ['markdownEditor.checkedList.title', 'markdownEditor.checkedList.aria'],
  help: ['markdownEditor.help.title', 'markdownEditor.help.aria'],
  preview: ['markdownEditor.preview.title', 'markdownEditor.preview.aria'],
  edit: ['markdownEditor.edit.title', 'markdownEditor.edit.aria'],
  live: ['markdownEditor.live.title', 'markdownEditor.live.aria'],
  fullscreen: ['markdownEditor.fullscreen.title', 'markdownEditor.fullscreen.aria'],
}

function applyLabels(command, t) {
  const keys = command.name ? MD_EDITOR_LABELS_BY_NAME[command.name] : undefined
  if (!keys || !command.buttonProps) return command
  const [titleKey, ariaKey] = keys
  return {
    ...command,
    buttonProps: {
      ...command.buttonProps,
      title: t(titleKey, { defaultValue: command.buttonProps.title || command.name }),
      'aria-label': t(ariaKey, {
        defaultValue: command.buttonProps['aria-label'] || command.buttonProps.title || command.name,
      }),
    },
  }
}

export function mdEditorCommandsFilter(command, locale = 'ru', t = (_key, params) => params?.defaultValue || _key) {
  if (!command) return command
  if (String(locale || 'ru').toLowerCase().startsWith('en')) return command
  let next = command
  if (Array.isArray(command.children)) {
    const mapped = command.children.map((child) => mdEditorCommandsFilter(child, locale, t))
    if (mapped.some((c, i) => c !== command.children[i])) {
      next = { ...command, children: mapped }
    }
  }
  let out = applyLabels(next, t)
  if (out.name === 'help') {
    out = {
      ...out,
      execute: () => {
        window.open(t('markdownEditor.help.url'), '_blank', 'noreferrer')
      },
    }
  }
  return out
}
