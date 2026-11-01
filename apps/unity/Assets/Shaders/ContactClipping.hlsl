#ifndef PROBEPLANNER_CONTACT_CLIPPING_INCLUDED
#define PROBEPLANNER_CONTACT_CLIPPING_INCLUDED

// Sampled CCF sides, not display-label orientation. Unknown sides grant no exception.
bool ContactSliceBypass(float worldX, half enabled, half4 sides)
{
    if (enabled < 0.5h) return false;
    if (worldX < 0.0) return sides.x > 0.5h;
    if (worldX > 0.0) return sides.y > 0.5h;
    return max(max(sides.x, sides.y), sides.z) > 0.5h;
}

#endif
