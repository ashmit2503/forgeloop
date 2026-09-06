import { CaretDown } from '@phosphor-icons/react/dist/csr/CaretDown'
import { CaretRight } from '@phosphor-icons/react/dist/csr/CaretRight'
import { CircleNotch } from '@phosphor-icons/react/dist/csr/CircleNotch'
import { FileCode } from '@phosphor-icons/react/dist/csr/FileCode'
import { FileText } from '@phosphor-icons/react/dist/csr/FileText'
import { Files } from '@phosphor-icons/react/dist/csr/Files'
import { Folder } from '@phosphor-icons/react/dist/csr/Folder'
import { List } from '@phosphor-icons/react/dist/csr/List'
import { Warning } from '@phosphor-icons/react/dist/csr/Warning'
import { X } from '@phosphor-icons/react/dist/csr/X'
import { useMemo, useState } from 'react'
import type { Attempt } from '../types/api'
import { createLineDiff, sourceLines } from '../lib/diff'
import { diagnosisLabel } from '../lib/diagnosis'
import { useResizableWidth } from '../hooks/useWorkspaceLayout'
import type { AttemptDiagnosis, WorkspaceFile } from '../types/workspace'

const MAX_RENDERED_LINES = 5_000
const MAX_DIFF_CHARACTERS = 250_000

export function CodeWorkspace({
  attempt,
  files,
  selectedFile,
  currentContent,
  previousContent,
  loading,
  liveStreaming = false,
  filter,
  diagnosis,
  onFile,
  onShowOutput,
  onDismissDiagnosis,
}: {
  attempt: Attempt | null
  files: WorkspaceFile[]
  selectedFile: string | null
  currentContent: string | null
  previousContent: string | null
  loading: boolean
  liveStreaming?: boolean
  filter: string
  diagnosis: AttemptDiagnosis | null
  onFile: (path: string) => void
  onShowOutput: () => void
  onDismissDiagnosis: () => void
}) {
  const [mobileFilesOpen, setMobileFilesOpen] = useState(false)
  const tree = useResizableWidth('coding-sandbox:file-tree-width:v1', 216, 160, 400)
  const selected = files.find((file) => file.path === selectedFile) || null
  const showDiff = Boolean(attempt && attempt.number > 1 && selected?.change)
  const visibleContent = currentContent
  const streamActive = liveStreaming
  const diffTruncated = Boolean(
    showDiff &&
    ((previousContent?.length || 0) > MAX_DIFF_CHARACTERS ||
      (visibleContent?.length || 0) > MAX_DIFF_CHARACTERS ||
      exceedsLineLimit(previousContent) ||
      exceedsLineLimit(visibleContent)),
  )
  const boundedPrevious = useMemo(() => boundedDiffContent(previousContent), [previousContent])
  const boundedCurrent = useMemo(() => boundedDiffContent(visibleContent), [visibleContent])
  const diff = useMemo(
    () => (showDiff ? createLineDiff(boundedPrevious, boundedCurrent) : []),
    [boundedCurrent, boundedPrevious, showDiff],
  )
  const filteredFiles = filter
    ? files.filter((file) => file.path.toLowerCase().includes(filter.toLowerCase()))
    : files
  const additions = diff.filter((line) => line.kind === 'added').length
  const removals = diff.filter((line) => line.kind === 'removed').length

  if (loading && !selectedFile) return <EditorSkeleton />

  return (
    <div className="code-workspace" style={{ '--file-tree-width': `${tree.width}px` } as React.CSSProperties}>
      <button className="mobile-files-button" type="button" onClick={() => setMobileFilesOpen(true)}>
        <List aria-hidden="true" />
        Files
      </button>
      <FileTree
        files={filteredFiles}
        selectedFile={selectedFile}
        open={mobileFilesOpen}
        onClose={() => setMobileFilesOpen(false)}
        onFile={(path) => {
          onFile(path)
          setMobileFilesOpen(false)
        }}
      />
      <div
        className="panel-resizer file-resizer"
        role="separator"
        aria-label="Resize file tree"
        aria-orientation="vertical"
        tabIndex={0}
        data-resize-direction="right"
        onPointerDown={tree.beginResize}
        onKeyDown={tree.resizeWithKeyboard}
      />
      <section
        className="editor"
        aria-label={
          selectedFile ? `${showDiff ? 'Repair diff' : 'Source'} for ${selectedFile}` : 'Code editor'
        }
      >
        <header className="editor-header">
          <div className="file-breadcrumb">
            {selectedFile ? <PathBreadcrumb path={selectedFile} /> : <span>No file selected</span>}
            {selected?.change ? (
              <span className={`change-badge ${selected.change}`}>{capitalize(selected.change)}</span>
            ) : null}
          </div>
          {streamActive ? (
            <span className="editor-streaming">
              <CircleNotch className="spinning" aria-hidden="true" />
              Streaming
            </span>
          ) : showDiff ? (
            <div className="diff-summary">
              <span>Compared with Attempt {Math.max(1, (attempt?.number || 1) - 1)}</span>
              <span className="addition">+{additions}</span>
              <span className="deletion">−{removals}</span>
            </div>
          ) : null}
        </header>
        {diagnosis && (attempt?.status === 'failed' || (attempt?.number && attempt.number > 1)) ? (
          <div className="diagnosis-strip" role="status">
            <Warning className="diagnosis-mark" weight="fill" aria-hidden="true" />
            <strong>{diagnosisLabel(diagnosis)}</strong>
            <span>{diagnosis.message}</span>
            <button type="button" onClick={onShowOutput}>
              View output
            </button>
            <button
              className="diagnosis-dismiss"
              type="button"
              onClick={onDismissDiagnosis}
              aria-label="Dismiss diagnosis"
            >
              <X aria-hidden="true" />
            </button>
          </div>
        ) : null}
        {loading ? (
          <EditorSkeleton compact />
        ) : selectedFile ? (
          showDiff && !streamActive ? (
            <DiffView lines={diff} truncated={diffTruncated} />
          ) : (
            <SourceView
              lines={sourceLines(visibleContent, MAX_RENDERED_LINES + 1)}
              missing={currentContent === null}
              streaming={streamActive}
            />
          )
        ) : (
          <div className="editor-empty">
            <FileCode aria-hidden="true" />
            <span>Files appear here after the agent writes code.</span>
          </div>
        )}
      </section>
    </div>
  )
}

function FileTree({
  files,
  selectedFile,
  open,
  onClose,
  onFile,
}: {
  files: WorkspaceFile[]
  selectedFile: string | null
  open: boolean
  onClose: () => void
  onFile: (path: string) => void
}) {
  const groups = groupFiles(files)
  return (
    <aside className={`file-tree${open ? ' mobile-open' : ''}`} aria-label="Workspace files">
      <div className="tree-header">
        <span>
          <Files aria-hidden="true" />
          Files
        </span>
        <span className="file-count">{files.length}</span>
        <button className="mobile-tree-close" type="button" onClick={onClose}>
          Close
        </button>
      </div>
      <div className="tree-root">
        {groups.map((group) => (
          <div className="file-group" key={group.folder || 'root'}>
            {group.folder ? (
              <div className="folder-row">
                <CaretDown aria-hidden="true" />
                <Folder aria-hidden="true" />
                <span>{group.folder}</span>
              </div>
            ) : null}
            <div className={group.folder ? 'tree-files' : 'root-files'}>
              {group.files.map((file) => (
                <FileRow file={file} active={file.path === selectedFile} onFile={onFile} key={file.path} />
              ))}
            </div>
          </div>
        ))}
      </div>
    </aside>
  )
}

function FileRow({
  file,
  active,
  onFile,
}: {
  file: WorkspaceFile
  active: boolean
  onFile: (path: string) => void
}) {
  const Icon = file.path.endsWith('.json') || file.path.endsWith('.md') ? FileText : FileCode
  return (
    <button
      className={`file-row${active ? ' active' : ''}${file.change === 'deleted' ? ' deleted' : ''}`}
      type="button"
      onClick={() => onFile(file.path)}
      title={file.path}
    >
      <Icon aria-hidden="true" />
      <span>{baseName(file.path)}</span>
      {file.change ? <small>{file.change.charAt(0).toUpperCase()}</small> : null}
    </button>
  )
}

function PathBreadcrumb({ path }: { path: string }) {
  const parts = path.split('/')
  return (
    <>
      {parts.map((part, index) => (
        <span className="breadcrumb-part" key={`${part}-${index}`}>
          {index ? <CaretRight aria-hidden="true" /> : null}
          <span className={index === parts.length - 1 ? 'current' : ''}>{part}</span>
        </span>
      ))}
    </>
  )
}

function DiffView({ lines, truncated }: { lines: ReturnType<typeof createLineDiff>; truncated: boolean }) {
  return (
    <div className="diff-code" role="table" aria-label="Inline code diff">
      {lines.slice(0, MAX_RENDERED_LINES).map((line, index) => (
        <div className={`diff-row ${line.kind}`} role="row" key={`${index}-${line.kind}`}>
          <span className="old-line" role="cell">
            {line.oldLine ?? ''}
          </span>
          <span className="new-line" role="cell">
            {line.newLine ?? ''}
          </span>
          <span className="diff-marker" role="cell">
            {line.kind === 'added' ? '+' : line.kind === 'removed' ? '−' : ' '}
          </span>
          <code role="cell">{line.content || '\u00a0'}</code>
        </div>
      ))}
      {truncated || lines.length > MAX_RENDERED_LINES ? (
        <p className="editor-limit">Showing the first 5,000 diff lines.</p>
      ) : null}
    </div>
  )
}

function SourceView({
  lines,
  missing,
  streaming,
}: {
  lines: ReturnType<typeof sourceLines>
  missing: boolean
  streaming: boolean
}) {
  if (missing)
    return (
      <div className="editor-empty">
        <FileCode aria-hidden="true" />
        <span>This file was deleted in the selected attempt.</span>
      </div>
    )
  return (
    <div className="source-code" role="table" aria-label="Source code">
      {lines.slice(0, MAX_RENDERED_LINES).map((line) => (
        <div className="source-row" role="row" key={line.number}>
          <span role="cell">{line.number}</span>
          <code role="cell">{line.content || '\u00a0'}</code>
        </div>
      ))}
      {lines.length > MAX_RENDERED_LINES ? (
        <p className="editor-limit">Showing the first 5,000 lines.</p>
      ) : null}
      {streaming ? (
        <div className="stream-cursor" role="row" aria-label="Code is streaming">
          <span />
          <code>▋</code>
        </div>
      ) : null}
    </div>
  )
}

function EditorSkeleton({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`editor-skeleton${compact ? ' compact' : ''}`} aria-label="Loading workspace">
      {Array.from({ length: 13 }, (_, index) => (
        <span style={{ width: `${36 + ((index * 17) % 52)}%` }} key={index} />
      ))}
    </div>
  )
}

function groupFiles(files: WorkspaceFile[]) {
  const groups = new Map<string, WorkspaceFile[]>()
  for (const file of files) {
    const slash = file.path.indexOf('/')
    const folder = slash >= 0 ? file.path.slice(0, slash) : ''
    const group = groups.get(folder) || []
    group.push(file)
    groups.set(folder, group)
  }
  return [...groups].map(([folder, groupedFiles]) => ({ folder, files: groupedFiles }))
}

function baseName(path: string) {
  return path.split('/').at(-1) || path
}
function capitalize(value: string) {
  return value.charAt(0).toUpperCase() + value.slice(1)
}

function boundedDiffContent(value: string | null): string | null {
  if (value === null) return null
  return value.slice(0, MAX_DIFF_CHARACTERS).split('\n', MAX_RENDERED_LINES).join('\n')
}

function exceedsLineLimit(value: string | null): boolean {
  return value !== null && value.split('\n', MAX_RENDERED_LINES + 1).length > MAX_RENDERED_LINES
}
