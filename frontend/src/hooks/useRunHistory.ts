import { useCallback, useRef, useState } from 'react'
import { api } from '../lib/api'
import type { Task } from '../types/api'

const PAGE_SIZE = 100
const API_LIMIT = 500

export function useRunHistory(notify: (message: string, tone: 'error') => void) {
  const [tasks, setTasks] = useState<Task[]>([])
  const [historyHasMore, setHistoryHasMore] = useState(false)
  const [historyLoadingMore, setHistoryLoadingMore] = useState(false)
  const tasksRef = useRef(tasks)
  tasksRef.current = tasks
  const loadingMore = useRef(false)
  const revision = useRef(0)

  const refreshTasks = useCallback(
    async (silent = false) => {
      if (loadingMore.current) return
      const requestId = ++revision.current
      const count = Math.max(PAGE_SIZE, tasksRef.current.length)
      try {
        const requests = Array.from({ length: Math.ceil((count + 1) / API_LIMIT) }, (_, index) => {
          const offset = index * API_LIMIT
          return api.tasks(Math.min(API_LIMIT, count + 1 - offset), offset)
        })
        const page = (await Promise.all(requests)).flat()
        if (requestId !== revision.current) return
        setTasks(page.slice(0, count))
        setHistoryHasMore(page.length > count)
      } catch {
        if (!silent && requestId === revision.current) notify('Could not load run history.', 'error')
      }
    },
    [notify],
  )

  const loadMoreTasks = useCallback(async () => {
    if (loadingMore.current || !historyHasMore) return
    loadingMore.current = true
    setHistoryLoadingMore(true)
    const requestId = ++revision.current
    try {
      const page = await api.tasks(PAGE_SIZE + 1, tasksRef.current.length)
      if (requestId !== revision.current) return
      setTasks((current) => {
        const known = new Set(current.map((task) => task.id))
        return [...current, ...page.slice(0, PAGE_SIZE).filter((task) => !known.has(task.id))]
      })
      setHistoryHasMore(page.length > PAGE_SIZE)
    } catch {
      if (requestId === revision.current) notify('Could not load older runs.', 'error')
    } finally {
      loadingMore.current = false
      setHistoryLoadingMore(false)
    }
  }, [historyHasMore, notify])

  return { tasks, setTasks, historyHasMore, historyLoadingMore, refreshTasks, loadMoreTasks }
}
