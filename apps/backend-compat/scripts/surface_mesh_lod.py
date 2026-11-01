"""Topology-preserving display LODs; no segmentation repair or anatomical claims."""
import numpy as np

try:
    from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray, vtk_to_numpy
    from vtkmodules.vtkCommonCore import vtkPoints, vtkVersion
    from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData
    from vtkmodules.vtkFiltersCore import vtkDecimatePro, vtkFeatureEdges, vtkPolyDataConnectivityFilter
except ImportError as exc:
    raise ImportError('Surface LOD building requires requirements-surface-build.txt; no coarse-grid fallback is used') from exc


def as_polydata(vertices, faces, normals):
    points = vtkPoints()
    points.SetData(numpy_to_vtk(np.asarray(vertices, dtype=np.float64), deep=True))
    cells = vtkCellArray()
    packed = np.column_stack((np.full(len(faces), 3, dtype=np.int64), faces)).ravel()
    cells.SetCells(len(faces), numpy_to_vtkIdTypeArray(packed, deep=True))
    mesh = vtkPolyData()
    mesh.SetPoints(points)
    mesh.SetPolys(cells)
    if normals is not None:
        mesh.GetPointData().SetNormals(numpy_to_vtk(np.asarray(normals, dtype=np.float32), deep=True))
    return mesh


def as_arrays(mesh):
    vertices = vtk_to_numpy(mesh.GetPoints().GetData()).copy()
    packed = vtk_to_numpy(mesh.GetPolys().GetData()).reshape(-1, 4)
    if not np.all(packed[:, 0] == 3):
        raise ValueError('Expected triangle mesh')
    normals = mesh.GetPointData().GetNormals()
    return vertices, packed[:, 1:].copy(), None if normals is None else vtk_to_numpy(normals).copy()


def mesh_topology(mesh):
    connectivity = vtkPolyDataConnectivityFilter()
    connectivity.SetInputData(mesh)
    connectivity.SetExtractionModeToAllRegions()
    connectivity.Update()
    result = {'components': int(connectivity.GetNumberOfExtractedRegions())}
    for key, boundary in [('boundaryEdges', True), ('nonManifoldEdges', False)]:
        edges = vtkFeatureEdges()
        edges.SetInputData(mesh)
        edges.FeatureEdgesOff()
        edges.ManifoldEdgesOff()
        edges.SetBoundaryEdges(boundary)
        edges.SetNonManifoldEdges(not boundary)
        edges.Update()
        result[key] = int(edges.GetOutput().GetNumberOfCells())
    return result


def simplify_surface(reference, step, reference_topology=None):
    if step < 1:
        raise ValueError('LOD density factor must be positive')
    reference_topology = reference_topology or mesh_topology(reference)
    reduction = 1.0 - 1.0 / (step * step)
    error = step / 4.0
    decimator = vtkDecimatePro()
    decimator.SetInputData(reference)
    decimator.SetTargetReduction(reduction)
    decimator.PreserveTopologyOn()
    decimator.SplittingOff()
    decimator.BoundaryVertexDeletionOff()
    decimator.AccumulateErrorOn()
    decimator.SetErrorIsAbsolute(True)
    decimator.SetAbsoluteError(error)
    decimator.Update()
    mesh = vtkPolyData()
    mesh.DeepCopy(decimator.GetOutput())
    topology = mesh_topology(mesh)
    if topology != reference_topology:
        raise ValueError(f'LOD changed topology: {reference_topology} -> {topology}')
    if not mesh.GetNumberOfCells():
        raise ValueError('LOD is empty')
    vertices, faces, normals = as_arrays(mesh)
    if not np.isfinite(vertices).all() or (normals is not None and not np.isfinite(normals).all()):
        raise ValueError('LOD has non-finite geometry')
    return vertices, faces, normals, dict(method='vtkDecimatePro', vtkVersion=vtkVersion.GetVTKVersion(),
        densityFactor=step, stepSize=1, requestedReduction=reduction,
        actualReduction=1.0 - len(faces) / reference.GetNumberOfPolys(),
        absoluteErrorControlVoxels=error, preserveTopology=True, splitting=False,
        boundaryVertexDeletion=False, accumulateError=True, topology=topology)
