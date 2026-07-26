import { LabProvider } from './store/LabStore'
import { AppShell } from './components/AppShell'

export default function App() {
  return (
    <LabProvider>
      <AppShell />
    </LabProvider>
  )
}
