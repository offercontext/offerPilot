import AuthGate from './components/AuthGate';
import AppShell from './layout/AppShell';

export default function App() {
  return (
    <AuthGate>
      <AppShell />
    </AuthGate>
  );
}
