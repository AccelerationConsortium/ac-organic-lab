// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useLumastirControl } from "./use-lumastir-control";
import { lumastirCommand } from "./lumastir-api";
vi.mock("./lumastir-api",()=>({lumastirCommand:vi.fn()}));
const send=vi.mocked(lumastirCommand);
beforeEach(()=>{vi.useFakeTimers();send.mockReset();send.mockImplementation(async(_id,action)=>action==="claim"?{claim_token:"token",heartbeat_interval_s:10}:{status:"ok"});});
afterEach(()=>{cleanup();vi.useRealTimers();});
it("shares one claim across outputs, heartbeats, then releases on unmount",async()=>{
 const {result,unmount}=renderHook(()=>useLumastirControl("lumastir",false,vi.fn()));
 await act(()=>result.current.command("motor/set",{index:0,speed:50}));
 await act(()=>result.current.command("led/set",{index:2,brightness:100}));
 expect(send.mock.calls.filter(c=>c[1]==="claim")).toHaveLength(1);
 expect(send.mock.calls.filter(c=>c[1]==="release")).toHaveLength(0);
 await act(()=>vi.advanceTimersByTimeAsync(5000));
 expect(send).toHaveBeenCalledWith("lumastir","heartbeat",{},"token");
 unmount();expect(send).toHaveBeenLastCalledWith("lumastir","release",{},"token",true);
});
it("does not replay a command after a heartbeat failure",async()=>{
 const error=new Error("lease lost"), report=vi.fn();
 const {result}=renderHook(()=>useLumastirControl("lumastir",false,report));
 await act(()=>result.current.command("motor/set",{index:0,speed:50}));
 send.mockImplementation(async(_id,action)=>{if(action==="heartbeat")throw error;return {status:"ok"};});
 await act(()=>vi.advanceTimersByTimeAsync(15000));
 expect(report).toHaveBeenCalledWith(error);
 expect(result.current.active).toBe(false);
 expect(send.mock.calls.filter(c=>c[1]==="motor/set")).toHaveLength(1);
 expect(send.mock.calls.filter(c=>c[1]==="claim")).toHaveLength(1);
});
it("releases a late claim without starting hardware after unmount",async()=>{
 let resolve:(value:unknown)=>void=()=>{};
 send.mockImplementationOnce(()=>new Promise(r=>{resolve=r;}));
 const {result,unmount}=renderHook(()=>useLumastirControl("lumastir",false,vi.fn()));
 let pending:Promise<void>;act(()=>{pending=result.current.command("motor/set",{index:0,speed:50});});
 unmount();await act(async()=>{resolve({claim_token:"late",heartbeat_interval_s:10});await pending;});
 expect(send.mock.calls.map(c=>c[1])).toEqual(["claim","release"]);
});
it("All off cancels an outstanding claim acquisition before it can start a motor",async()=>{
 let resolve:(value:unknown)=>void=()=>{};
 send.mockImplementationOnce(()=>new Promise(r=>{resolve=r;}));
 const {result}=renderHook(()=>useLumastirControl("lumastir",false,vi.fn()));
 let pending:Promise<void>;act(()=>{pending=result.current.command("motor/set",{index:0,speed:50});});
 await act(()=>result.current.command("stop"));
 await act(async()=>{resolve({claim_token:"late",heartbeat_interval_s:10});await pending;});
 expect(send.mock.calls.map(c=>c[1])).toEqual(["claim","stop","release"]);
 expect(result.current.active).toBe(false);
});
it("refreshes live status immediately after a successful setpoint",async()=>{
 const refresh=vi.fn(async()=>{});
 const {result}=renderHook(()=>useLumastirControl("lumastir",false,vi.fn(),refresh));
 await act(()=>result.current.command("motor/set",{index:0,speed:50}));
 expect(refresh).toHaveBeenCalledTimes(1);
 expect(send.mock.calls.filter(c=>c[1]==="release")).toHaveLength(0);
});
it("does not refresh from a refused command until release is acknowledged",async()=>{
 const refresh=vi.fn(async()=>{});
 send.mockImplementation(async(_id,action)=>{
  if(action==="claim")return {claim_token:"token",heartbeat_interval_s:10};
  if(action==="motor/set")throw new Error("Refused");
  if(action==="release")throw new Error("Network unavailable");
  return {status:"ok"};
 });
 const {result}=renderHook(()=>useLumastirControl("lumastir",false,vi.fn(),refresh));
 await act(()=>result.current.command("motor/set",{index:0,speed:50}));
 expect(refresh).not.toHaveBeenCalled();
});
