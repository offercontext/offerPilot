import { StrictMode, Suspense, lazy } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider, App as AntApp } from 'antd';
import zhCN from 'antd/locale/zh_CN';
const App = lazy(() => import('./App'));
const DesktopHaruWindow = lazy(() => import('./features/assistantSurface/DesktopHaruWindow'));
import { isDesktopHaru } from './features/assistantSurface/desktopHaru';
import { ThemeProvider, useThemeMode } from './theme/ThemeContext';
import { lightTheme, darkTheme } from './theme/antdTheme';
import './theme/tokens.css';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { refetchOnWindowFocus: false, retry: 1 },
  },
});

function ThemedApp() {
  const { mode } = useThemeMode();
  return (
    <ConfigProvider locale={zhCN} theme={mode === 'dark' ? darkTheme : lightTheme}>
      <AntApp>
        <Suspense fallback={null}>{isDesktopHaru() ? <DesktopHaruWindow /> : <App />}</Suspense>
      </AntApp>
    </ConfigProvider>
  );
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <ThemedApp />
      </ThemeProvider>
    </QueryClientProvider>
  </StrictMode>
);
