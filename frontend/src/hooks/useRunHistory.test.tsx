import { act, renderHook } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '../lib/api'
import type { Task } from '../types/api'
import { useRunHistory } from './useRunHistory'

afterEach(() => vi.restoreAllMocks())

it('refreshes all loaded history without resurrecting the load-more button or retaining deleted tasks', async () => {
  let saved = Array.from({ length: 140 }, (_, index) => ({ id: `task-${index}` }) as Task)
  const requests = vi
    .spyOn(api, 'tasks')
    .mockImplementation(async (limit = 100, offset = 0) => saved.slice(offset, offset + limit))
  const { result } = renderHook(() => useRunHistory(vi.fn()))
  await act(() => result.current.refreshTasks())
  expect(result.current.tasks).toHaveLength(100)
  await act(() => result.current.loadMoreTasks())
  expect(result.current.tasks).toHaveLength(140)
  expect(result.current.historyHasMore).toBe(false)

  saved = saved.filter((task) => task.id !== 'task-130')
  await act(() => result.current.refreshTasks())
  expect(requests).toHaveBeenLastCalledWith(141, 0)
  expect(result.current.tasks).toHaveLength(139)
  expect(result.current.historyHasMore).toBe(false)
})
