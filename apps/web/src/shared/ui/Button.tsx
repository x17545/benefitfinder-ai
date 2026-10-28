import type { ButtonHTMLAttributes, JSX } from 'react';
import './ui.css';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** primary: 진한 네이비 버튼, secondary: 연한 버튼 */
  variant?: 'primary' | 'secondary';
  /** large: 화면 아래 큰 버튼(56px), medium: 카드 안 버튼(46px) */
  size?: 'large' | 'medium';
  /** true면 가로로 꽉 채운다 */
  block?: boolean;
}

export function Button({
  variant = 'primary',
  size = 'medium',
  block = false,
  className,
  type = 'button',
  ...rest
}: ButtonProps): JSX.Element {
  const classes = [
    'ui-button',
    `ui-button--${variant}`,
    size === 'large' ? 'ui-button--large' : '',
    block ? 'ui-button--block' : '',
    className ?? '',
  ]
    .filter((name) => name !== '')
    .join(' ');

  return <button type={type} className={classes} {...rest} />;
}
