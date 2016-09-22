# Sample plugin showing how to add a plugin to some Jenkins jobs
#
# This plugin adds a cobertura coverage publisher post-build-action to all jobs
# that contain at least one package that ends with 'unittests'.

from xml.etree import ElementTree

PLUGIN = ElementTree.fromstring("""
    <hudson.plugins.cobertura.CoberturaPublisher plugin="cobertura@1.9.8">
      <coberturaReportFile>**/coverage.xml</coberturaReportFile>
      <onlyStable>false</onlyStable>
      <failUnhealthy>false</failUnhealthy>
      <failUnstable>false</failUnstable>
      <autoUpdateHealth>false</autoUpdateHealth>
      <autoUpdateStability>false</autoUpdateStability>
      <zoomCoverageChart>false</zoomCoverageChart>
      <maxNumberOfBuilds>0</maxNumberOfBuilds>
      <failNoReports>true</failNoReports>
      <healthyTarget>
        <targets class="enum-map" enum-type="hudson.plugins.cobertura.targets.CoverageMetric">
          <entry>
            <hudson.plugins.cobertura.targets.CoverageMetric>METHOD</hudson.plugins.cobertura.targets.CoverageMetric>
            <int>8000000</int>
          </entry>
          <entry>
            <hudson.plugins.cobertura.targets.CoverageMetric>LINE</hudson.plugins.cobertura.targets.CoverageMetric>
            <int>8000000</int>
          </entry>
          <entry>
            <hudson.plugins.cobertura.targets.CoverageMetric>CONDITIONAL</hudson.plugins.cobertura.targets.CoverageMetric>
            <int>7000000</int>
          </entry>
        </targets>
      </healthyTarget>
      <unhealthyTarget>
        <targets class="enum-map" enum-type="hudson.plugins.cobertura.targets.CoverageMetric">
          <entry>
            <hudson.plugins.cobertura.targets.CoverageMetric>METHOD</hudson.plugins.cobertura.targets.CoverageMetric>
            <int>0</int>
          </entry>
          <entry>
            <hudson.plugins.cobertura.targets.CoverageMetric>LINE</hudson.plugins.cobertura.targets.CoverageMetric>
            <int>0</int>
          </entry>
          <entry>
            <hudson.plugins.cobertura.targets.CoverageMetric>CONDITIONAL</hudson.plugins.cobertura.targets.CoverageMetric>
            <int>0</int>
          </entry>
        </targets>
      </unhealthyTarget>
      <failingTarget>
        <targets class="enum-map" enum-type="hudson.plugins.cobertura.targets.CoverageMetric">
          <entry>
            <hudson.plugins.cobertura.targets.CoverageMetric>METHOD</hudson.plugins.cobertura.targets.CoverageMetric>
            <int>0</int>
          </entry>
          <entry>
            <hudson.plugins.cobertura.targets.CoverageMetric>LINE</hudson.plugins.cobertura.targets.CoverageMetric>
            <int>0</int>
          </entry>
          <entry>
            <hudson.plugins.cobertura.targets.CoverageMetric>CONDITIONAL</hudson.plugins.cobertura.targets.CoverageMetric>
            <int>0</int>
          </entry>
        </targets>
      </failingTarget>
      <sourceEncoding>ASCII</sourceEncoding>
    </hudson.plugins.cobertura.CoberturaPublisher>
""")

def checkCobertura(config, checkoutSteps, buildSteps, packageSteps, **kwargs):
    found = False
    for s in checkoutSteps:
        if s.getPackage().getName().endswith("unittests"): found = True
    for s in buildSteps:
        if s.getPackage().getName().endswith("unittests"): found = True
    for s in packageSteps:
        if s.getPackage().getName().endswith("unittests"): found = True

    if found:
        root = ElementTree.fromstring(config)
        publishers = root.find("publishers")
        if publishers.find("hudson.plugins.cobertura.CoberturaPublisher") is None:
            publishers.append(PLUGIN)
            config = ElementTree.tostring(root, encoding="UTF-8")

    return config

manifest = {
    'apiVersion' : "0.3",
    'hooks' : {
        'jenkinsJobCreate' : checkCobertura,
        'jenkinsJobPostUpdate' : checkCobertura
    }
}
