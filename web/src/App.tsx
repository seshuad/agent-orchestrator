import { useEffect, useState } from 'react'
import { Route, Routes } from 'react-router-dom'
import { api, type Session } from './api'
import { AppBar, SessionContext } from './ui'
import Home from './pages/Home'
import NewAgent from './pages/NewAgent'
import RunsPage from './pages/Runs'
import Approvals from './pages/Approvals'
import Connections from './pages/Connections'
import ConnectionForm from './pages/ConnectionForm'
import Connectors from './pages/Connectors'
import McpConnector from './pages/McpConnector'
import Editor from './editor/Editor'

export default function App() {
  const [session, setSession] = useState<Session | null>(null)
  const [approvals, setApprovals] = useState(0)
  useEffect(() => {
    const load = () => {
      api.session().then(setSession).catch(() => {})
      api.approvals().then((a) => setApprovals(a.length)).catch(() => {})
    }
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [])
  return (
    <SessionContext.Provider value={session}>
      <div className="app">
        <AppBar approvals={approvals} />
        <div className="app-main">
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/new" element={<NewAgent />} />
            <Route path="/agents/:name" element={<Editor />} />
            <Route path="/agents/:name/runs" element={<RunsPage />} />
            <Route path="/agents/:name/runs/:runId" element={<RunsPage />} />
            <Route path="/runs" element={<RunsPage />} />
            <Route path="/runs/:runId" element={<RunsPage />} />
            <Route path="/approvals" element={<Approvals />} />
            <Route path="/connections" element={<Connections />} />
            <Route path="/connections/connectors" element={<Connectors />} />
            <Route path="/connections/connectors/:id" element={<McpConnector />} />
            <Route path="/connections/:id" element={<ConnectionForm />} />
          </Routes>
        </div>
      </div>
    </SessionContext.Provider>
  )
}
