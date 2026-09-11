// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { EquipmentSnapshot } from "@/types/api";
import { LumastirTile } from "./LumastirTile";
const mocks=vi.hoisted(()=>({command:vi.fn(),locked:false,busy:false,refresh:undefined as undefined | (()=>Promise<void>),getStatus:vi.fn()}));
vi.mock("@/lib/use-control-lock",()=>({useControlLock:()=>({locked:mocks.locked,countdown:0,toggle:vi.fn()})}));
vi.mock("@/lib/use-lumastir-control",()=>({useLumastirControl:(_id: string,_locked: boolean,_report: unknown,refresh:()=>Promise<void>)=>{mocks.refresh=refresh;return {command:mocks.command,busy:mocks.busy,active:false};}}));
vi.mock("@/lib/api",async importOriginal=>({...await importOriginal<typeof import("@/lib/api")>(),getEquipmentStatus:mocks.getStatus}));
vi.mock("./TileShell",()=>({TileShell:({children}: {children:React.ReactNode})=><div>{children}</div>}));
function snapshot(speed=0):EquipmentSnapshot {
 return {id:"lumastir",name:"Lumastir",kind:"other",enabled:true,fetched_at:new Date().toISOString(),fetch_error:null,
  status:{equipment_status:"ready",allowed_actions:["lumastir.motor.set","lumastir.led.set"],details:{max_power:100,
   motors:[0,4,8].map((address,index)=>({id:`motor_${address}`,index,commanded_percent:index===0?speed:0})),
   leds:[17,18,27].map((address,index)=>({id:`led_${address}`,index,commanded_percent:0}))}}} as unknown as EquipmentSnapshot;
}
beforeEach(()=>{mocks.command.mockReset();mocks.getStatus.mockReset();mocks.locked=false;mocks.busy=false;});afterEach(cleanup);
it("renders three columns and sends separate motor and LED setpoints",()=>{
 render(<LumastirTile snapshot={snapshot()} />);
 expect(screen.getAllByRole("region")).toHaveLength(3);
 fireEvent.change(screen.getByLabelText("Vial 2 stir percentage"),{target:{value:"65"}});
 fireEvent.click(screen.getByRole("button",{name:"Vial 2 motor on"}));
 expect(mocks.command).toHaveBeenLastCalledWith("motor/set",{index:1,speed:65});
 fireEvent.click(screen.getByRole("button",{name:"Vial 3 LED on"}));
 expect(mocks.command).toHaveBeenLastCalledWith("led/set",{index:2,brightness:100});
});
it("turns off only the selected motor",()=>{
 render(<LumastirTile snapshot={snapshot(50)} />);
 fireEvent.click(screen.getByRole("button",{name:"Vial 1 motor off"}));
 expect(mocks.command).toHaveBeenCalledWith("motor/set",{index:0,speed:0});
});
it("blocks invalid percentages, unknown states, stale data and unauthorized controls",()=>{
 const {rerender}=render(<LumastirTile snapshot={snapshot()} />);
 fireEvent.change(screen.getByLabelText("Vial 1 stir percentage"),{target:{value:"101"}});
 expect((screen.getByRole("button",{name:"Vial 1 motor on"}) as HTMLButtonElement).disabled).toBe(true);
 const s=snapshot(); s.fetched_at="2020-01-01T00:00:00Z";rerender(<LumastirTile snapshot={s} />);
 expect((screen.getByRole("button",{name:"Vial 2 motor on"}) as HTMLButtonElement).disabled).toBe(true);
 mocks.locked=true;rerender(<LumastirTile snapshot={snapshot()} />);
 expect((screen.getByRole("button",{name:"Stop all Lumastir motors and LEDs"}) as HTMLButtonElement).disabled).toBe(true);
});
it("keeps All off available during an in-flight action",()=>{
 mocks.busy=true;render(<LumastirTile snapshot={snapshot(50)} />);
 fireEvent.click(screen.getByRole("button",{name:"Stop all Lumastir motors and LEDs"}));
 expect(mocks.command).toHaveBeenCalledWith("stop",{});
});
it("shows fresh motor and LED state immediately and ignores older fleet polls",async()=>{
 const old=snapshot();old.fetched_at=new Date(Date.now()-2000).toISOString();
 const fresh=snapshot(50);
 (fresh.status.details!.leds as {commanded_percent:number}[])[0].commanded_percent=100;
 mocks.getStatus.mockResolvedValue(fresh);
 const {rerender}=render(<LumastirTile snapshot={old} />);
 await act(()=>mocks.refresh!());
 expect(mocks.getStatus).toHaveBeenCalledWith("lumastir");
 expect(screen.getByRole("button",{name:"Vial 1 motor off"})).toBeTruthy();
 expect(screen.getByRole("button",{name:"Vial 1 LED off"})).toBeTruthy();
 rerender(<LumastirTile snapshot={{...old,fetched_at:new Date(Date.now()-1000).toISOString()}} />);
 expect(screen.getByRole("button",{name:"Vial 1 motor off"})).toBeTruthy();
 rerender(<LumastirTile snapshot={{...snapshot(),fetched_at:new Date(Date.now()+1000).toISOString()}} />);
 expect(screen.getByRole("button",{name:"Vial 1 motor on"})).toBeTruthy();
});
it("ignores an out-of-order status response after a newer read",async()=>{
 let resolve:(value:EquipmentSnapshot)=>void=()=>{};
 mocks.getStatus.mockImplementationOnce(()=>new Promise(r=>{resolve=r;})).mockResolvedValueOnce(snapshot());
 render(<LumastirTile snapshot={snapshot()} />);
 let earlier:Promise<void>;act(()=>{earlier=mocks.refresh!();});
 await act(()=>mocks.refresh!());
 await act(async()=>{resolve(snapshot(50));await earlier;});
 expect(screen.getByRole("button",{name:"Vial 1 motor on"})).toBeTruthy();
});
