// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { CameraPlayer } from './CameraPlayer';
vi.mock('@/lib/go2rtc',()=>({mseSupported:()=>true}));
vi.mock('./MsePlayer',()=>({MsePlayer:()=> <div data-testid="live">Live</div>}));
vi.mock('./WebRtcPlayer',()=>({WebRtcPlayer:()=> <div data-testid="rtc">RTC</div>}));
afterEach(()=>{cleanup();vi.useRealTimers();Object.defineProperty(document,'hidden',{configurable:true,value:false});});

it('stops a hidden ordinary tab and resumes only on visibility',()=>{
  vi.useFakeTimers();
  Object.defineProperty(document,'hidden',{configurable:true,value:false});
  render(<CameraPlayer src="/streams/api/ws?src=cam_main" />);
  expect(screen.queryByTestId('live')).not.toBeNull();
  act(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));});
  act(()=>vi.advanceTimersByTime(3000));
  expect(screen.queryByTestId('live')).toBeNull();
  act(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'));});
  expect(screen.queryByTestId('live')).not.toBeNull();
});
it('allows the approved-monitor client path in a hidden tab (server still verifies approval)',()=>{
  Object.defineProperty(document,'hidden',{configurable:true,value:true});
  render(<CameraPlayer src="/streams/api/ws?src=cam_main" grantId="approval" />);
  expect(screen.queryByTestId('live')).not.toBeNull();
});
