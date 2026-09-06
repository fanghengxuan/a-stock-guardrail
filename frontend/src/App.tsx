import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom"
import { ReportStreamProvider } from "./ReportStreamProvider"
import WorkspacePage from "./pages/WorkspacePage"
import HistoryPage from "./pages/HistoryPage"
import ReportPage from "./pages/ReportPage"

function App() {
  return (
    <ReportStreamProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<WorkspacePage />} />
          <Route path="/reports" element={<HistoryPage />} />
          <Route path="/reports/:code" element={<ReportPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </ReportStreamProvider>
  )
}

export default App
