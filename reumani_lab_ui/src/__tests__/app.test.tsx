import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, within, fireEvent, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from '../App'

// Reumani Lab UI-0 — mock prototype tests.
// These assert interaction behaviour on mock data only. No component makes network/LLM calls.

beforeEach(() => {
  localStorage.clear()
  // jsdom lacks these; stub so mock download / clipboard don't throw.
  if (!URL.createObjectURL) Object.defineProperty(URL, 'createObjectURL', { value: vi.fn(() => 'blob:mock'), writable: true })
  if (!URL.revokeObjectURL) Object.defineProperty(URL, 'revokeObjectURL', { value: vi.fn(), writable: true })
})
afterEach(() => cleanup())

function answerFirstOpenClarification() {
  const groups = screen.getAllByRole('radiogroup')
  const card = groups[0]
  const radios = within(card).getAllByRole('radio')
  fireEvent.click(radios[0])
  const submit = within(card.closest('.clar') as HTMLElement).getByRole('button', { name: '提交澄清' })
  fireEvent.click(submit)
}

describe('shell & navigation', () => {
  it('1. renders the four-zone workbench with product name in the rail', () => {
    render(<App />)
    expect(screen.getByLabelText('Reumani Lab logo')).toBeInTheDocument()
    expect(screen.getByLabelText('任务工作区')).toBeInTheDocument()
  })

  it('2. switching project updates the subtitle', async () => {
    render(<App />)
    const select = screen.getByLabelText('当前项目') as HTMLSelectElement
    expect(screen.getByText(/secretory immunity/i)).toBeInTheDocument()
    await userEvent.selectOptions(select, 'proj-ssc-fibro')
    expect(screen.getByText(/Single-cell & mechanism/i)).toBeInTheDocument()
  })

  it('3. switching task updates the header title', () => {
    render(<App />)
    fireEvent.click(screen.getByRole('option', { name: /CRISPR guide RNA design/ }))
    expect(screen.getByRole('heading', { level: 1, name: /CRISPR guide RNA design/ })).toBeInTheDocument()
  })
})

describe('file assets', () => {
  it('4. mock upload adds a file to the list', async () => {
    render(<App />)
    const input = screen.getByTestId('file-input') as HTMLInputElement
    const file = new File(['x'], 'my_dataset.csv', { type: 'text/csv' })
    await userEvent.upload(input, file)
    expect(screen.getByText('my_dataset.csv')).toBeInTheDocument()
  })

  it('5. deleting a file requires confirmation', () => {
    render(<App />)
    expect(screen.getByText('il6_mrss_cohort.csv')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /删除 il6_mrss_cohort.csv/ }))
    // dialog shown; cancel keeps the file
    const dialog = screen.getByRole('dialog', { name: '删除文件' })
    fireEvent.click(within(dialog).getByRole('button', { name: '取消' }))
    expect(screen.getByText('il6_mrss_cohort.csv')).toBeInTheDocument()
    // now confirm
    fireEvent.click(screen.getByRole('button', { name: /删除 il6_mrss_cohort.csv/ }))
    fireEvent.click(within(screen.getByRole('dialog', { name: '删除文件' })).getByRole('button', { name: '删除' }))
    expect(screen.queryByText('il6_mrss_cohort.csv')).not.toBeInTheDocument()
  })

  it('6. file search filters the list', async () => {
    render(<App />)
    await userEvent.type(screen.getByLabelText('搜索文件'), 'fasta')
    expect(screen.getByText('stat3_locus.fasta')).toBeInTheDocument()
    expect(screen.queryByText('il6_mrss_cohort.csv')).not.toBeInTheDocument()
  })
})

describe('clarification core interaction', () => {
  it('7. submit is disabled until an option is selected', () => {
    render(<App />)
    const card = screen.getAllByRole('radiogroup')[0].closest('.clar') as HTMLElement
    expect(within(card).getByRole('button', { name: '提交澄清' })).toBeDisabled()
  })

  it('8. after selecting an option submit is enabled and card becomes answered', () => {
    render(<App />)
    const card = screen.getAllByRole('radiogroup')[0].closest('.clar') as HTMLElement
    fireEvent.click(within(card).getAllByRole('radio')[0])
    const submit = within(card).getByRole('button', { name: '提交澄清' })
    expect(submit).toBeEnabled()
    fireEvent.click(submit)
    expect(screen.getAllByText('澄清已回答').length).toBeGreaterThan(0)
  })

  it('9. answering a clarification decreases the todo count', () => {
    render(<App />)
    expect(screen.getByTestId('todo-count').textContent).toBe('3')
    answerFirstOpenClarification()
    expect(screen.getByTestId('todo-count').textContent).toBe('2')
  })

  it('10. answering a clarification adds a timeline event', () => {
    render(<App />)
    const before = within(screen.getByTestId('timeline')).getAllByRole('listitem').length
    answerFirstOpenClarification()
    const after = within(screen.getByTestId('timeline')).getAllByRole('listitem').length
    expect(after).toBeGreaterThan(before)
    expect(screen.getAllByText('澄清已回答').length).toBeGreaterThan(0)
  })

  it('11. answering BOTH clarifications unblocks the plan step (blocked → running)', () => {
    render(<App />)
    // step-3 objective row initially Blocked
    answerFirstOpenClarification()
    answerFirstOpenClarification()
    // both clarifications answered → step-3 should now be running
    const stepRow = screen.getByText(/判断因果强度/).closest('.step') as HTMLElement
    expect(within(stepRow).getByText('Running')).toBeInTheDocument()
  })
})

describe('trace, artifacts, runtime, composer', () => {
  it('12. execution trace toggles open and closed', () => {
    render(<App />)
    expect(screen.queryByTestId('trace-panel')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Show execution trace' }))
    expect(screen.getByTestId('trace-panel')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Hide execution trace' }))
    expect(screen.queryByTestId('trace-panel')).not.toBeInTheDocument()
  })

  it('13. artifact preview opens with desensitized content', () => {
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: /预览 Evidence report.md/ }))
    const dlg = screen.getByTestId('artifact-preview')
    expect(within(dlg).getByText(/证据分级/)).toBeInTheDocument()
  })

  it('14. mock download uses a Blob, not a network fetch', () => {
    const fetchSpy = vi.fn()
    vi.stubGlobal('fetch', fetchSpy)
    const createSpy = vi.spyOn(URL, 'createObjectURL')
    render(<App />)
    const artCard = screen.getByRole('button', { name: /预览 analysis_table.csv/ }).closest('.art') as HTMLElement
    fireEvent.click(within(artCard).getByRole('button', { name: '下载（mock）' }))
    expect(createSpy).toHaveBeenCalled()
    expect(fetchSpy).not.toHaveBeenCalled()
    vi.unstubAllGlobals()
  })

  it('15. runtime clock is shown', () => {
    render(<App />)
    expect(screen.getByTestId('runtime-clock').textContent).toMatch(/^\d{2}:\d{2}$/)
  })

  it('16. Stop moves runtime to stopped and adds a stopped timeline event', () => {
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: '■ Stop' }))
    expect(screen.getByRole('button', { name: '▶ Resume' })).toBeInTheDocument()
    expect(within(screen.getByTestId('timeline')).getAllByText('已停止').length).toBeGreaterThan(0)
  })

  it('17. Resume returns runtime to running', () => {
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: '■ Stop' }))
    fireEvent.click(screen.getByRole('button', { name: '▶ Resume' }))
    expect(screen.getByRole('button', { name: '■ Stop' })).toBeInTheDocument()
  })

  it('18. composer send appends a user message to the timeline', async () => {
    render(<App />)
    await userEvent.type(screen.getByLabelText('消息输入框'), '请分析 IL-6 数据')
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    expect(within(screen.getByTestId('timeline')).getByText('请分析 IL-6 数据')).toBeInTheDocument()
  })
})

describe('safety: no external I/O', () => {
  it('19. no fetch/XHR is issued during a full interaction pass', async () => {
    const fetchSpy = vi.fn()
    vi.stubGlobal('fetch', fetchSpy)
    const xhrOpen = vi.spyOn(XMLHttpRequest.prototype, 'open')
    render(<App />)
    answerFirstOpenClarification()
    fireEvent.click(screen.getByRole('button', { name: 'Show execution trace' }))
    await userEvent.type(screen.getByLabelText('消息输入框'), 'hello')
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    expect(fetchSpy).not.toHaveBeenCalled()
    expect(xhrOpen).not.toHaveBeenCalled()
    vi.unstubAllGlobals()
    xhrOpen.mockRestore()
  })

  it('20. persists user-driven state to localStorage under a namespaced key', () => {
    render(<App />)
    answerFirstOpenClarification()
    const raw = localStorage.getItem('reumani-lab-ui-v1')
    expect(raw).toBeTruthy()
    expect(JSON.parse(raw!).clarifications.some((c: { answered: boolean }) => c.answered)).toBe(true)
  })
})
