import { Routes, Route } from 'react-router-dom';
import { Navbar }        from './components/Navbar';
import { DashboardPage }  from './pages/DashboardPage';
import { SearchPage }     from './pages/SearchPage';
import { WikiPage }       from './pages/WikiPage';
import { GraphPage }      from './pages/GraphPage';
import { IngestPage }     from './pages/IngestPage';
import { IntrospectPage } from './pages/IntrospectPage';
import { EvalsPage }      from './pages/EvalsPage';
import { NotFoundPage }   from './pages/NotFoundPage';

export function App() {
  return (
    <>
      <Navbar />
      <main id="main-content" className="mx-auto max-w-screen-2xl px-4 py-4">
        <Routes>
          <Route path="/"           element={<DashboardPage />} />
          <Route path="/search"     element={<SearchPage />} />
          <Route path="/wiki/*"     element={<WikiPage />} />
          <Route path="/graph"      element={<GraphPage />} />
          <Route path="/ingest"     element={<IngestPage />} />
          <Route path="/introspect" element={<IntrospectPage />} />
          <Route path="/evals"      element={<EvalsPage />} />
          <Route path="*"           element={<NotFoundPage />} />
        </Routes>
      </main>
    </>
  );
}
