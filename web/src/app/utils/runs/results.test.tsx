// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import RunsPage from "./page";
afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.history.replaceState(null,"","/"); });
it("retries retained recording without starting another equipment run", async () => {
  window.history.replaceState(null,"","/?run_id=run-1");
  let pending=true;
  const posts:string[]=[];
  vi.stubGlobal("fetch",vi.fn(async (url:string,init?:RequestInit) => {
    if (init?.method==="POST") { posts.push(url); pending=false; }
    const body=url.endsWith('/measurements/retry') ? {state:'published'}
      : url.endsWith('/measurements') ? {state:pending?'pending':'published',error:pending?'Record server unavailable':null,captured_steps:46,measurements:[]}
      : url.endsWith('/manual') ? {requests:[],live:false}
      : {run_id:'run-1',status:'finished',waiting_on:null,abort_requested:null};
    return new Response(JSON.stringify(body),{status:200});
  }));
  const client=new QueryClient({defaultOptions:{queries:{retry:false},mutations:{retry:false}}});
  render(<QueryClientProvider client={client}><RunsPage /></QueryClientProvider>);
  await screen.findByText('Record server unavailable');
  fireEvent.click(screen.getByRole('button',{name:'Retry saving measurements'}));
  await waitFor(() => expect(screen.getByText(/Recording: published/)).toBeTruthy());
  expect(posts).toEqual(['/api/workflow/runs/run-1/measurements/retry']);
  client.clear();
});
