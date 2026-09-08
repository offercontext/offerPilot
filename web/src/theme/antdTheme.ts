import { theme as antdAlgorithms, type ThemeConfig } from 'antd';

const sharedFont =
  '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "HarmonyOS Sans SC", "Microsoft YaHei", "Helvetica Neue", Arial, sans-serif';

const sharedToken = {
  colorPrimary: '#6366f1',
  colorSuccess: '#059669',
  colorWarning: '#d97706',
  colorError: '#ef4444',
  colorInfo: '#6366f1',
  borderRadius: 12,
  fontFamily: sharedFont,
  fontSize: 14,
};

export const lightTheme: ThemeConfig = {
  algorithm: antdAlgorithms.defaultAlgorithm,
  token: {
    ...sharedToken,
    colorBgLayout: '#f6f6fb',
    colorBgContainer: '#ffffff',
    colorText: '#23213a',
    colorTextSecondary: '#8b87b3',
    colorBorderSecondary: '#ecebf5',
    boxShadow: '0 2px 10px rgba(99,102,241,0.08)',
    boxShadowSecondary: '0 8px 28px rgba(99,102,241,0.14)',
  },
  components: {
    Button: {
      primaryShadow: '0 4px 12px rgba(99,102,241,0.30)',
      controlHeight: 36,
      borderRadius: 8,
    },
    Input: { controlHeight: 36, borderRadius: 8 },
    InputNumber: { controlHeight: 36, borderRadius: 8 },
    Select: { controlHeight: 36, borderRadius: 8 },
    Tag: { borderRadiusSM: 8 },
    Alert: { borderRadiusLG: 8 },
    Card: { borderRadiusLG: 14 },
    Modal: { borderRadiusLG: 16 },
    Segmented: { borderRadius: 10 },
  },
};

export const darkTheme: ThemeConfig = {
  algorithm: antdAlgorithms.darkAlgorithm,
  token: {
    ...sharedToken,
    colorBgLayout: '#141322',
    colorBgContainer: '#1c1a2e',
    colorText: '#e7e5f2',
    colorTextSecondary: '#9a95bf',
    colorBorderSecondary: '#2a2740',
  },
  components: {
    Button: { controlHeight: 36, borderRadius: 8 },
    Input: { controlHeight: 36, borderRadius: 8 },
    InputNumber: { controlHeight: 36, borderRadius: 8 },
    Select: { controlHeight: 36, borderRadius: 8 },
    Tag: { borderRadiusSM: 8 },
    Alert: { borderRadiusLG: 8 },
    Card: { borderRadiusLG: 14 },
    Modal: { borderRadiusLG: 16 },
  },
};
