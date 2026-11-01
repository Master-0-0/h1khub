<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { EditorState } from '@codemirror/state'
import { EditorView, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter, placeholder as placeholderExt } from '@codemirror/view'
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'
import { sql as sqlLang } from '@codemirror/lang-sql'
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language'
import { tags as t } from '@lezer/highlight'
import { autocompletion, closeBrackets } from '@codemirror/autocomplete'
import { state } from '../lib/store'

const props = defineProps({
  modelValue: { type: String, default: '' },
  dialect: { type: String, default: 'mysql' },
  readonly: { type: Boolean, default: false },
  minHeight: { type: String, default: '120px' },
  placeholder: { type: String, default: 'SELECT * FROM …' },
})
const emit = defineEmits(['update:modelValue', 'run'])
const host = ref(null)
let view = null
let updating = false

const highlightDark = HighlightStyle.define([
  { tag: t.keyword, color: '#c792ea' },
  { tag: [t.string, t.special(t.string)], color: '#c3e88d' },
  { tag: [t.number, t.bool, t.null], color: '#f78c6c' },
  { tag: [t.comment, t.lineComment, t.blockComment], color: '#697098', fontStyle: 'italic' },
  { tag: [t.typeName, t.className], color: '#ffcb6b' },
  { tag: [t.propertyName, t.attributeName], color: '#82aaff' },
  { tag: [t.operator, t.punctuation], color: '#89ddff' },
  { tag: t.variableName, color: '#d7dae0' },
  { tag: t.function(t.variableName), color: '#82aaff' },
])

const themeDark = EditorView.theme(
  {
    '&': { backgroundColor: 'var(--bg)', color: 'var(--text)', height: '100%' },
    '.cm-scroller': { fontFamily: 'var(--mono)', lineHeight: '1.55' },
    '.cm-content': { caretColor: 'var(--accent)', padding: '6px 0' },
    '.cm-gutters': { backgroundColor: 'var(--bg-panel)', color: 'var(--text-4)', border: 'none' },
    '.cm-activeLine': { backgroundColor: 'rgba(255,255,255,0.025)' },
    '.cm-activeLineGutter': { backgroundColor: 'rgba(255,255,255,0.035)', color: 'var(--text-2)' },
    '.cm-selectionBackground, &.cm-focused .cm-selectionBackground, ::selection': { backgroundColor: 'var(--accent-weak)' },
    '.cm-cursor, .cm-dropCursor': { borderLeftColor: 'var(--accent)' },
    '.cm-tooltip': { backgroundColor: 'var(--bg-elev)', border: '1px solid var(--border-strong)', borderRadius: '6px' },
    '.cm-tooltip-autocomplete ul li[aria-selected]': { backgroundColor: 'var(--accent-weak)', color: 'var(--text)' },
    '&.cm-focused': { outline: 'none' },
  },
  { dark: true },
)

function build() {
  if (!host.value) return
  const dark = (state.settings.appearance?.theme || 'dark') !== 'light'
  const editorCfg = state.settings.editor || {}
  const fontSize = Number(editorCfg.fontSize) || 13
  const tabSize = Number(editorCfg.tabSize) || 2
  const wordWrap = !!editorCfg.wordWrap
  const editorPrefs = EditorView.theme({ '.cm-content': { fontSize: `${fontSize}px` } })
  view = new EditorView({
    parent: host.value,
    state: EditorState.create({
      doc: props.modelValue || '',
      extensions: [
        lineNumbers(),
        highlightActiveLine(),
        highlightActiveLineGutter(),
        history(),
        closeBrackets(),
        autocompletion(),
        keymap.of([
          { key: 'Mod-Enter', run: () => (emit('run'), true) },
          { key: 'Shift-Enter', run: () => (emit('run'), true) },
          ...defaultKeymap,
          ...historyKeymap,
          indentWithTab,
        ]),
        EditorState.readOnly.of(props.readonly),
        EditorView.editable.of(!props.readonly),
        EditorState.tabSize.of(tabSize),
        editorPrefs,
        ...(wordWrap ? [EditorView.lineWrapping] : []),
        sqlLang(),
        placeholderExt(props.placeholder),
        themeDark,
        syntaxHighlighting(highlightDark),
        EditorView.updateListener.of((update) => {
          if (update.docChanged && !updating) {
            emit('update:modelValue', update.state.doc.toString())
          }
        }),
      ],
    }),
  })
}

onMounted(build)

watch(
  () => props.modelValue,
  (value) => {
    if (!view) return
    const current = view.state.doc.toString()
    if (value === current) return
    updating = true
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: value || '' } })
    updating = false
  },
)

onBeforeUnmount(() => {
  view?.destroy()
  view = null
})

defineExpose({
  focus: () => view?.focus(),
  insert: (text) => {
    if (!view) return
    const pos = view.state.selection.main.head
    view.dispatch({ changes: { from: pos, insert: text }, selection: { anchor: pos + text.length } })
    view.focus()
  },
  getSelection: () => {
    if (!view) return ''
    const range = view.state.selection.main
    return range.empty ? '' : view.state.sliceDoc(range.from, range.to)
  },
  getDoc: () => view?.state.doc.toString() || '',
})
</script>

<template>
  <div class="cm-wrap" :style="{ minHeight }" ref="host" />
</template>
