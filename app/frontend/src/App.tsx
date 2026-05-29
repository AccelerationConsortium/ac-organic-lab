import { lazy, Suspense } from 'react'
import { Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import ErrorBoundary from './components/ErrorBoundary'
import LoadingSpinner from './components/LoadingSpinner'

const Overview     = lazy(() => import('./pages/Overview'))
const LiveMonitor  = lazy(() => import('./pages/LiveMonitor'))
const Energy       = lazy(() => import('./pages/Energy'))
const History      = lazy(() => import('./pages/History'))
const Alerts       = lazy(() => import('./pages/Alerts'))
const WorkflowPage = lazy(() => import('./pages/WorkflowPage'))
const HtePlatform  = lazy(() => import('./pages/HtePlatform'))
const NotFound     = lazy(() => import('./pages/NotFound'))

export default function App() {
  return (
    <ErrorBoundary>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index                  element={<Suspense fallback={<LoadingSpinner />}><Overview /></Suspense>} />
          <Route path="live"            element={<Suspense fallback={<LoadingSpinner />}><LiveMonitor /></Suspense>} />
          <Route path="energy"          element={<Suspense fallback={<LoadingSpinner />}><Energy /></Suspense>} />
          <Route path="history"         element={<Suspense fallback={<LoadingSpinner />}><History /></Suspense>} />
          <Route path="alerts"          element={<Suspense fallback={<LoadingSpinner />}><Alerts /></Suspense>} />
          <Route path="workflow"        element={<Suspense fallback={<LoadingSpinner />}><WorkflowPage /></Suspense>} />
          <Route path="platforms/hte"   element={<Suspense fallback={<LoadingSpinner />}><HtePlatform /></Suspense>} />
          <Route path="*"               element={<Suspense fallback={<LoadingSpinner />}><NotFound /></Suspense>} />
        </Route>
      </Routes>
    </ErrorBoundary>
  )
}
