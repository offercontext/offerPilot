import { createRoot } from 'react-dom/client';
import { BackupRestore } from '../../src/components/BackupRestore';
createRoot(document.getElementById('root')!).render(<main style={{ maxWidth: 900, margin: '32px auto', padding: 16 }}><BackupRestore /></main>);
