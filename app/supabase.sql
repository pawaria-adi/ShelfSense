-- ShelfSense — run once in Supabase: SQL Editor -> New query -> paste -> Run.
-- Accounts come from Supabase Auth (email + password). Each shop has members
-- (owner / staff); a member can only read and change their own shop's data.
-- For a hackathon: Authentication -> Providers -> Email -> turn OFF "Confirm email".

create extension if not exists pgcrypto;

-- shops and who belongs to them ---------------------------------------------
create table if not exists public.shops (
  id          uuid primary key default gen_random_uuid(),
  name        text not null check (length(name) between 1 and 80),
  join_code   text unique not null default upper(substr(md5(gen_random_uuid()::text), 1, 6)),
  created_by  uuid references auth.users on delete set null default auth.uid(),
  created_at  timestamptz default now()
);
create table if not exists public.shop_members (
  shop_id      uuid references public.shops on delete cascade,
  user_id      uuid references auth.users on delete cascade,
  role         text not null default 'staff' check (role in ('owner', 'staff')),
  display_name text,
  joined_at    timestamptz default now(),
  primary key (shop_id, user_id)
);
alter table public.shop_members add column if not exists mobile text;   -- 10 digits, shown to teammates

-- every shared document ("<shop id>/shop/stock", "<shop id>/deliveries/...") ---
create table if not exists public.docs (
  path         text primary key,
  data         jsonb,
  lease_holder text,
  lease_until  timestamptz,
  updated_at   timestamptz default now()
);

create or replace function public.is_member(p_shop text) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from shop_members m where m.shop_id::text = p_shop and m.user_id = auth.uid());
$$;
create or replace function public.is_owner(p_shop uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from shop_members m where m.shop_id = p_shop and m.user_id = auth.uid() and m.role = 'owner');
$$;

drop function if exists public.create_shop(text, text);
drop function if exists public.join_shop(text, text);
create or replace function public.create_shop(p_name text, p_display text, p_mobile text)
returns public.shops language plpgsql security definer set search_path = public as $$
declare s shops;
begin
  if auth.uid() is null then raise exception 'sign in first'; end if;
  insert into shops(name, created_by) values (trim(p_name), auth.uid()) returning * into s;
  insert into shop_members(shop_id, user_id, role, display_name, mobile)
    values (s.id, auth.uid(), 'owner', p_display, nullif(regexp_replace(coalesce(p_mobile, ''), '\D', '', 'g'), ''));
  return s;
end $$;

create or replace function public.join_shop(p_code text, p_display text, p_mobile text)
returns public.shops language plpgsql security definer set search_path = public as $$
declare s shops;
begin
  if auth.uid() is null then raise exception 'sign in first'; end if;
  select * into s from shops where join_code = upper(trim(p_code));
  if s.id is null then raise exception 'no shop with that code'; end if;
  insert into shop_members(shop_id, user_id, role, display_name, mobile)
    values (s.id, auth.uid(), 'staff', p_display, nullif(regexp_replace(coalesce(p_mobile, ''), '\D', '', 'g'), ''))
    on conflict (shop_id, user_id) do update set display_name = excluded.display_name, mobile = excluded.mobile;
  return s;
end $$;

create or replace function public.set_display(p_shop uuid, p_display text) returns void
language sql security definer set search_path = public as $$
  update shop_members set display_name = p_display where shop_id = p_shop and user_id = auth.uid();
$$;

create or replace function public.new_join_code(p_shop uuid) returns text
language plpgsql security definer set search_path = public as $$
declare c text := upper(substr(md5(gen_random_uuid()::text), 1, 6));
begin
  if not is_owner(p_shop) then raise exception 'only the owner can do this'; end if;
  update shops set join_code = c where id = p_shop;
  return c;
end $$;

create or replace function public.remove_member(p_shop uuid, p_user uuid) returns void
language plpgsql security definer set search_path = public as $$
begin
  if not is_owner(p_shop) then raise exception 'only the owner can do this'; end if;
  if p_user = auth.uid() then raise exception 'the owner cannot remove themselves'; end if;
  delete from shop_members where shop_id = p_shop and user_id = p_user;
end $$;

create or replace function public.rename_shop(p_shop uuid, p_name text) returns void
language plpgsql security definer set search_path = public as $$
begin
  if not is_owner(p_shop) then raise exception 'only the owner can do this'; end if;
  update shops set name = trim(p_name) where id = p_shop;
end $$;

-- One phone at a time may change a document (a short "lease"), so two phones
-- syncing at once can't overwrite each other's changes.
create or replace function public.acquire_lease(p_path text, p_holder text, p_ttl_ms int)
returns boolean language plpgsql security definer set search_path = public as $$
begin
  if not is_member(split_part(p_path, '/', 1)) then raise exception 'not a member of this shop'; end if;
  insert into docs(path, data) values (p_path, null) on conflict (path) do nothing;
  update docs
     set lease_holder = p_holder,
         lease_until  = now() + make_interval(secs => greatest(1000, least(p_ttl_ms, 600000)) / 1000.0)
   where path = p_path
     and (lease_holder is null or lease_holder = p_holder or lease_until < now());
  return found;
end $$;

-- access rules -----------------------------------------------------------------
alter table public.shops        enable row level security;
alter table public.shop_members enable row level security;
alter table public.docs         enable row level security;

drop policy if exists "members see their shop"  on public.shops;
drop policy if exists "members see teammates"   on public.shop_members;
drop policy if exists "members read docs"       on public.docs;
drop policy if exists "members add docs"        on public.docs;
drop policy if exists "members change docs"     on public.docs;
create policy "members see their shop" on public.shops for select using (is_member(id::text));
create policy "members see teammates"  on public.shop_members for select using (is_member(shop_id::text));
create policy "members read docs"      on public.docs for select using (is_member(split_part(path, '/', 1)));
create policy "members add docs"       on public.docs for insert with check (is_member(split_part(path, '/', 1)));
create policy "members change docs"    on public.docs for update using (is_member(split_part(path, '/', 1)));

revoke all on public.docs, public.shops, public.shop_members from anon;
grant select on public.shops, public.shop_members to authenticated;
grant select, insert, update on public.docs to authenticated;
grant execute on function public.create_shop(text, text, text), public.join_shop(text, text, text), public.set_display(uuid, text),
  public.new_join_code(uuid), public.remove_member(uuid, uuid), public.rename_shop(uuid, text),
  public.acquire_lease(text, text, int) to authenticated;
