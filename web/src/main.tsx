import { StrictMode, Suspense, lazy } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider, App as AntApp } from 'antd';
import zhCN from 'antd/locale/zh_CN';
const App = lazy(() => import('./App'));
const DesktopHaruWindow = lazy(() => import('./features/assistantSurface/DesktopHaruWindow'));
import { isDesktopHaru } from './features/assistantSurface/desktopHaru';
import enUS from 'antd/locale/en_US';
import { useLocale, initializeLocale, t } from './i18n';
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
  const locale = useLocale();
  return (
    <ConfigProvider locale={locale === 'zh-CN' ? zhCN : enUS} theme={mode === 'dark' ? darkTheme : lightTheme}>
      <AntApp>
        <Suspense fallback={null}>{isDesktopHaru() ? <DesktopHaruWindow /> : <App />}</Suspense>
      </AntApp>
    </ConfigProvider>
  );
}

const root = createRoot(document.getElementById('root')!);
function mountApp() {
root.render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <ThemedApp />
      </ThemeProvider>
    </QueryClientProvider>
  </StrictMode>
);

}
void initializeLocale().then(mountApp).catch(() => {
  root.render(<section role="alert"><p>{t('无法读取桌面语言设置，请重新打开应用。')}</p><button type="button" onClick={() => window.location.reload()}>{t('重试')}</button></section>);
});
