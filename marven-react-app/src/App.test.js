import { render, screen } from '@testing-library/react';
import App from './App';

jest.mock('./pages/Home', () => () => <div>Marven home</div>);
jest.mock('./pages/RichChat', () => () => <div>Marven rich chat</div>);

test('renders Marven navigation and the home route', () => {
  render(<App />);
  expect(screen.getByRole('link', { name: /home/i })).toBeInTheDocument();
  expect(screen.getByRole('link', { name: /rich chat/i })).toBeInTheDocument();
  expect(screen.getByText('Marven home')).toBeInTheDocument();
});
