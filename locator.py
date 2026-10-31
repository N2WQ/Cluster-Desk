"""Approximate paths between Maidenhead grid-square centers."""
import math
import re


def grid_center(grid):
    if not grid.isascii(): raise ValueError('Enter a four- or six-character ASCII Maidenhead grid, such as FN31 or FN31PR.')
    grid=grid.strip().upper()
    if not re.fullmatch(r'[A-R]{2}[0-9]{2}(?:[A-X]{2})?',grid):
        raise ValueError('Enter a four- or six-character Maidenhead grid, such as FN31 or FN31PR.')
    lon=-180+(ord(grid[0])-65)*20+int(grid[2])*2
    lat=-90+(ord(grid[1])-65)*10+int(grid[3])
    lon_width,lat_width=2.0,1.0
    if len(grid)==6:
        lon_width/=24;lat_width/=24
        lon+=(ord(grid[4])-65)*lon_width;lat+=(ord(grid[5])-65)*lat_width
    return lat+lat_width/2,lon+lon_width/2


def grid_path(origin, destination):
    return coordinate_path(grid_center(origin),grid_center(destination))


def coordinate_path(origin, destination):
    lat1,lon1=map(math.radians,origin);lat2,lon2=map(math.radians,destination)
    delta=lon2-lon1
    h=math.sin((lat2-lat1)/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin(delta/2)**2
    distance=6371.0088*2*math.asin(math.sqrt(min(1,max(0,h))))
    y=math.sin(delta)*math.cos(lat2)
    x=math.cos(lat1)*math.sin(lat2)-math.sin(lat1)*math.cos(lat2)*math.cos(delta)
    bearing=None if math.hypot(x,y)<1e-12 else math.degrees(math.atan2(y,x))%360
    return distance,bearing
