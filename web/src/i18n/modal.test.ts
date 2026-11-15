// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { Modal, type ModalFuncProps } from 'antd';
import { localizedModal } from './modal';
import { setLocale, t } from './index';

afterEach(() => { vi.restoreAllMocks(); setLocale('zh-CN'); });
it('updates an open confirmation without approving, closing, or recreating it', () => {
  setLocale('zh-CN');
  const update = vi.fn();
  const destroy = vi.fn();
  const approve = vi.fn();
  const afterClose = vi.fn();
  const confirm = vi.spyOn(Modal, 'confirm').mockReturnValue({ update, destroy });
  localizedModal('confirm', () => ({ title: t('删除这项偏好？'), onOk: approve, afterClose }));
  expect(confirm).toHaveBeenCalledTimes(1);
  expect(confirm.mock.calls[0][0].title).toBe('删除这项偏好？');
  setLocale('en-US');
  expect(update).toHaveBeenCalledTimes(1);
  expect(update.mock.calls[0][0].title).toBe('Delete this preference?');
  expect(approve).not.toHaveBeenCalled();
  expect(destroy).not.toHaveBeenCalled();
  setLocale('zh-CN');
  expect(update.mock.calls[1][0].title).toBe('删除这项偏好？');
  (update.mock.calls[1][0] as ModalFuncProps).afterClose?.();
  expect(afterClose).toHaveBeenCalledTimes(1);
  setLocale('en-US');
  expect(update).toHaveBeenCalledTimes(2);
});
