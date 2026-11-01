import { reactive } from 'vue'

export const ctxState = reactive({ open: false, x: 0, y: 0, items: [] })

export function showContextMenu(event, items) {
  event.preventDefault?.()
  event.stopPropagation?.()
  const x = event.clientX ?? event.x ?? 0
  const y = event.clientY ?? event.y ?? 0
  ctxState.items = items.filter(Boolean)
  ctxState.x = x
  ctxState.y = y
  ctxState.open = true
}

export function hideContextMenu() {
  ctxState.open = false
  ctxState.items = []
}

export const dialogState = reactive({
  confirm: null,
  prompt: null,
})

export function confirmDialog(options) {
  const opts = typeof options === 'string' ? { message: options } : { ...options }
  return new Promise((resolve) => {
    dialogState.confirm = {
      title: opts.title || '请确认',
      message: opts.message || '',
      detail: opts.detail || '',
      okText: opts.okText || '确定',
      cancelText: opts.cancelText || '取消',
      danger: !!opts.danger,
      resolve: (value) => {
        dialogState.confirm = null
        resolve(value)
      },
    }
  })
}

export function promptDialog(options) {
  const opts = typeof options === 'string' ? { message: options } : { ...options }
  return new Promise((resolve) => {
    dialogState.prompt = {
      title: opts.title || '输入',
      message: opts.message || '',
      value: opts.value || '',
      placeholder: opts.placeholder || '',
      multiline: !!opts.multiline,
      okText: opts.okText || '确定',
      resolve: (value) => {
        dialogState.prompt = null
        resolve(value)
      },
    }
  })
}
