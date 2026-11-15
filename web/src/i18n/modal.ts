import { Modal, type ModalFuncProps } from 'antd';
import { subscribeLocale, t } from './index';

/** Refresh authored dialog copy in place; never dismiss, replay, or approve it. */
export function localizedModal(kind: 'confirm' | 'info', options: () => ModalFuncProps) {
  let unsubscribe = () => {};
  function currentOptions(): ModalFuncProps {
    const current = options();
    return {
      ...current,
      okText: current.okText ?? t('确认'),
      cancelText: current.cancelText ?? t('取消'),
      afterClose: () => { unsubscribe(); current.afterClose?.(); },
    };
  }
  const instance = Modal[kind](currentOptions());
  unsubscribe = subscribeLocale(() => instance.update(currentOptions()));
  return instance;
}
