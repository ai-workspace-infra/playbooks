{{- /*
workload-autoscaling (canonical copy: oci/charts/library/workload-autoscaling).
Do not edit synced copies; run scripts/sync-autoscaling-helper.sh.

Usage (templates/autoscaling.yaml):
  {{ include "workload-autoscaling.render" (dict "root" . "files" (list "deployment.yaml")) }}
  "files" are this chart's workload templates; they are rendered and parsed so every
  Deployment/StatefulSet/DaemonSet they emit (incl. loops/conditionals) is covered, and
  an HPA the chart already renders for a workload is detected (externalHpa).
  Alternatively pass "workloads" (list (dict "name" "<name>" "kind" "Deployment")).

Values (per chart):
  vpa:  enabled (default true; rendered only when the VPA API exists in the cluster),
        updateMode, minReplicas, controlledValues, minAllowed, maxAllowed, workloads (optional override)
  hpa:  enabled (default false), minReplicas, maxReplicas,
        targetCPUUtilizationPercentage, targetMemoryUtilizationPercentage
Workload entries: name, kind (Deployment|StatefulSet|DaemonSet), optional externalHpa
(true when the chart renders its own HPA for that workload: no HPA is rendered here,
but the VPA still yields to it).
Mutual exclusion: when an HPA scales on CPU or memory, the VPA for the same workload
is forced to updateMode "Off" (recommendations only). DaemonSets get no HPA.
*/ -}}
{{- define "workload-autoscaling.render" -}}
{{- $root := .root -}}
{{- $vpa := default dict $root.Values.vpa -}}
{{- $hpa := default dict $root.Values.hpa -}}
{{- $workloads := default list .workloads -}}
{{- if .files -}}
  {{- $found := list -}}
  {{- $scaled := dict -}}
  {{- range $f := .files -}}
    {{- range $doc := splitList "\n---" (include (print $root.Template.BasePath "/" $f) $root) -}}
      {{- $o := fromYaml $doc -}}
      {{- if and $o (kindIs "map" $o) $o.kind -}}
        {{- if has $o.kind (list "Deployment" "StatefulSet" "DaemonSet") -}}
          {{- $found = append $found (dict "name" $o.metadata.name "kind" $o.kind) -}}
        {{- else if eq $o.kind "HorizontalPodAutoscaler" -}}
          {{- $_ := set $scaled $o.spec.scaleTargetRef.name true -}}
        {{- end -}}
      {{- end -}}
    {{- end -}}
  {{- end -}}
  {{- range $w := $found -}}
    {{- $workloads = append $workloads (merge $w (dict "externalHpa" (hasKey $scaled $w.name))) -}}
  {{- end -}}
{{- end -}}
{{- $workloads = default $workloads $vpa.workloads -}}
{{- $hpaOn := and (hasKey $hpa "enabled") $hpa.enabled -}}
{{- $hpaResource := or $hpa.targetCPUUtilizationPercentage $hpa.targetMemoryUtilizationPercentage -}}
{{- $vpaOn := ternary true $vpa.enabled (not (hasKey $vpa "enabled")) -}}
{{- $vpaApi := $root.Capabilities.APIVersions.Has "autoscaling.k8s.io/v1/VerticalPodAutoscaler" -}}
{{- range $w := $workloads }}
{{- $kind := default "Deployment" $w.kind }}
{{- $yield := or $w.externalHpa (and $hpaOn $hpaResource) }}
{{- if and $vpaOn $vpaApi }}
---
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata:
  name: {{ $w.name }}
  labels:
    app.kubernetes.io/managed-by: {{ $root.Release.Service }}
    app.kubernetes.io/instance: {{ $root.Release.Name }}
spec:
  targetRef:
    apiVersion: apps/v1
    kind: {{ $kind }}
    name: {{ $w.name }}
  updatePolicy:
    updateMode: {{ ternary "Off" (default "InPlaceOrRecreate" $vpa.updateMode) (and $yield (ne $kind "DaemonSet")) | quote }}
    minReplicas: {{ default 1 $vpa.minReplicas }}
  resourcePolicy:
    containerPolicies:
    - containerName: "*"
      controlledValues: {{ default "RequestsAndLimits" $vpa.controlledValues }}
      {{- with $vpa.minAllowed }}
      minAllowed:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with $vpa.maxAllowed }}
      maxAllowed:
        {{- toYaml . | nindent 8 }}
      {{- end }}
{{- end }}
{{- if and $hpaOn (not $w.externalHpa) (ne $kind "DaemonSet") }}
{{- if gt (int (default 1 $hpa.minReplicas)) (int (default 1 $hpa.maxReplicas)) }}{{ fail "hpa.minReplicas exceeds hpa.maxReplicas" }}{{ end }}
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: {{ $w.name }}
  labels:
    app.kubernetes.io/managed-by: {{ $root.Release.Service }}
    app.kubernetes.io/instance: {{ $root.Release.Name }}
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: {{ $kind }}
    name: {{ $w.name }}
  minReplicas: {{ default 1 $hpa.minReplicas }}
  maxReplicas: {{ default 1 $hpa.maxReplicas }}
  metrics:
  {{- with $hpa.targetCPUUtilizationPercentage }}
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: {{ . }}
  {{- end }}
  {{- with $hpa.targetMemoryUtilizationPercentage }}
  - type: Resource
    resource:
      name: memory
      target:
        type: Utilization
        averageUtilization: {{ . }}
  {{- end }}
{{- end }}
{{- end }}
{{- end -}}

{{- /* Replica count for a workload: omitted when the HPA owns it. */ -}}
{{- define "workload-autoscaling.replicas" -}}
{{- $hpa := default dict .Values.hpa -}}
{{- if not (and (hasKey $hpa "enabled") $hpa.enabled) -}}
replicas: {{ default 1 .Values.replicaCount }}
{{- end -}}
{{- end -}}
