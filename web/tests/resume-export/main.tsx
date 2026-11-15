// Test host only: all export, transport, PDF worker and render logic is production code.
import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import ResumeExportModal from '../../src/components/ResumeExportModal';
import type { Resume } from '../../src/types/resume';
const saved = (id: number) => ({ id, title: '中文简历' } as Resume);
function Fixture() {
  const [resume, setResume] = useState(() => saved(1));
  const [blocked, setBlocked] = useState(false);
  useEffect(() => {
    const change = (event: Event) => {
      const detail = (event as CustomEvent<{ id?: number; blocked?: boolean }>).detail;
      if (detail.id !== undefined) setResume(saved(detail.id));
      if (detail.blocked !== undefined) setBlocked(detail.blocked);
    };
    window.addEventListener('resume-fixture', change);
    return () => window.removeEventListener('resume-fixture', change);
  }, []);
  return <ResumeExportModal resume={resume} blocked={blocked} onClose={() => setResume(saved(1))} />;
}
createRoot(document.getElementById('root')!).render(<Fixture />);
