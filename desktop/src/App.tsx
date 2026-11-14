import {HostProvider} from './host/Host';
import WorkbenchMount from './plugins/WorkbenchMount';
import HostRecovery from './host/Recovery';
export default function App() {
  return <HostProvider><WorkbenchMount/><HostRecovery/></HostProvider>;
}
